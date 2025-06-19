import serial
import time
import datetime
import csv
import json
import threading
import logging
import inspect


class main_manager:
    def __init__(self, config_path):
        self.config_path = config_path
        self.code_manager = CodeManager(config_path)
        self.config_manager = self.code_manager.config_manager
        self.error_handler = self.code_manager.error_handler
        self.supervisor = self.code_manager.thread_manager
        self.experiment_manager = ExperimentManager(
            self.config_manager, self.error_handler, self.supervisor
        )

    def start(self):
        self.experiment_manager.start()


class CodeManager:
    def __init__(self, config):
        self.config_manager = ConfigManager(config)
        self.logger = logging.getLogger("CodeSupervisor")
        self.error_handler = ErrorHandler(self.logger, self.config_manager)
        self.thread_manager = TreadManager(manual_interception=True)

    def recovery_needed(self):
        return not self.config_manager.recovery_needed()

    def recover(self):
        self.logger.info("Starting recovery logic")

        last_status = self.config_manager.recovery_parameters_parser()
        default_params = self.config_manager.default_parameters_parser()

        cycle_begin = datetime.fromisoformat(
            last_status["current_cycle_begin"])
        cycle_end = datetime.fromisoformat(last_status["current_cycle_end"])
        cycle_duration = default_params.get("cycle_duration_hours", 12)

        now = datetime.datetime.now()

        if now < cycle_end:
            new_status = {
                "current_temp": last_status['current_temp'],
                "current_cycle_begin": cycle_begin.isoformat(),
                "current_cycle_end": cycle_end.isoformat(),
                "end_cycle_earlier": True}
        else:

            while cycle_begin + datetime.timedelta(hours=cycle_duration) < now:
                cycle_begin += datetime.timedelta(hours=cycle_duration)

            cycle_end = cycle_begin + datetime.timedelta(hours=cycle_duration)

            current_temp = 4

            new_status = {
                "current_temp": current_temp,
                "current_cycle_begin": cycle_begin.isoformat(),
                "current_cycle_end": cycle_end.isoformat(),
                "end_cycle_earlier": True
            }

        self.config_manager.update_status(new_status)
        self.logger.info("Recovery parameters updated and written to JSON")

    def before_start(self):
        tasks = self.config_manager.tasks_parser()
        return tasks

    def handle_error(self, method, exception):
        self.error_handler.manage_error(method, exception)


class ConfigManager:
    def __init__(self, config_path):
        self.config_path = config_path
        self.config = self.open_file(config_path)

    def open_file(self, config_path):
        with open(config_path) as config_file:
            return json.load(config_file)

    def recovery_needed(self):
        return self.config.get("end_with_error", False)

    def tasks_parser(self):
        default_params = self.config["default_parameters"]
        tasks_def = default_params["tasks"]
        parsed_tasks = []
        for task_def in tasks_def:
            cls, args, kwargs = self._parse_task(task_def)
            parsed_tasks.append((cls, args, kwargs))
        return parsed_tasks

    def _parse_task(self, task_def):
        cls_name, args_dict = task_def
        cls = globals()[cls_name]
        args = []
        kwargs = args_dict
        return cls, args, kwargs

    def recovery_parametrs_parser(self):
        return self.config.get("current_status", {})

    def default_parameters_parser(self):
        return self.config.get("default_parameters", {})

    def update_status(self, new_status: dict):
        self.config["current_status"] = new_status
        self.save_config()

    def save_config(self):
        with open(self.config_path, 'w') as f:
            json.dump(self.config, f, indent=4)


class ErrorHandler:
    def __init__(self):
        pass

    def mark_error_end(self):
        self.config_manager.update_file({"end_with_error": False})

    def log_error(self, method, error):
        self.logger.info(
            f"Whlie running {method} the error appeared. Try to restore experiment. Erorr - {error}")

    def message_about_error(self):
        pass

    def handle_error(self, method, error):
        self.mark_error()
        self.log_error(method, error)
        self.message_about_error


class TreadManager:
    def __init__(self, manual_interseption=True):
        self.stop_event = threading.Event()

    def start(self):
        threading.Thread(target=self.keyboard_listener, daemon=True).start()

    def keyboard_listener(self):
        input("Press enter to stop...\n")
        self.stop_event.set()


class ExperimentManager:
    def __init__(self, config):
        self.config = config
        self.runner = None
        self.guardian = CodeManager(config)

    def start(self):
        if self.guardian.recovery_needed:
            self.guardian.recovery()
        try:
            self.runner = self.guardian.before_start()
            self.runner.start()
            self.guardian.after_start()
        except Exception as e:
            self.guardian.handle_error(e)
            raise


class ExperimentStateManager:
    def __init__(self, config_path):
        self.config_path = config_path
        with open(config_path) as f:
            self.config = json.load(f)

    def was_crash_detected(self):
        return not self.config.get("end_with_error", False)

    def mark_success(self):
        self.config["end_with_error"] = False
        with open(self.config_path, "w") as f:
            json.dump(self.config, f, indent=4)

    def mark_failed(self):
        self.config["end_with_error"] = True
        with open(self.config_path, "w") as f:
            json.dump(self.config, f, indent=4)

    def update_current_status(self, current_temp, cycle_begin, cycle_end):
        self.config["current_status"] = {
            "current_temp": current_temp,
            "current_cycle_begin": cycle_begin,
            "current_cycle_end": cycle_end,
        }
        with open(self.config_path, "w") as f:
            json.dump(self.config, f, indent=4)


class ExperimentRunner:
    def __init__(self, tasks, manual_interception=True):
        self.tasks = []
        self.original_tasks = tasks
        self.threads = []
        self.objs = []
        self.threads_objects = []
        self.logger = logging.getLogger(__name__)
        if manual_interception:
            self.interceptor = ProccessIntereptor()
            self.interceptor.start()
            self.stop_event = self.interceptor.stop_event
        else:
            self.interceptor = None
            self.stop_event = None
        for cls, args, kwargs in tasks:
            kwargs = kwargs.copy()
            if 'stop_event' in inspect.signature(cls.__init__).parameters:
                kwargs.setdefault('stop_event', self.stop_event)
            self.tasks.append((cls, args, kwargs))

    def start(self):

        try:
            for cls, args, Kwargs in self.tasks:
                obj = cls(*args, **Kwargs)
                name = getattr(cls, "name", obj.__class__.__name__)
                self.objs.append(name)
                t = threading.Thread(target=obj.start)
                t.start()
                self.threads_objects.append(t)
                time.sleep(20)
            self.logger.info('Experiment starts succesfully')
        except Exception as e:
            self.logger.info(f'Error while started the experiment. {e}')


class TemperatureLogger:
    def __init__(
            self,
            controller,
            logger,
            filename='temperature_log.csv',
            interval=60,
            period_days=60,
            stop_event=None):
        self.field_names = ['Data and time', 'Temperature']
        self.filename = filename
        with open(self.filename, 'w', newline='') as temp_log:
            logging_file = csv.writer(temp_log)
            logging_file.writerow(self.field_names)
        self.interval = interval
        self.controller = controller
        self.period_days = period_days
        self.logger = logger
        self.name = 'TemperatureLogger'
        self.stop_event = stop_event

    def start(self):
        self.logger.info(f'stop_event received: {self.stop_event is not None}')

        self.logger.info('Starting periodic temperature recording')
        self.periodic_request()

    def get_temperature(self):
        self.logger.info('Temperature request')
        temperature = self.controller.get_water_bath_temperature()
        return temperature

    def write_temperature_to_file(self, temperature):
        with open(self.filename, 'a', newline='') as temp_log:
            logging_file = csv.writer(temp_log)
            logging_file.writerow([datetime.datetime.now(), temperature])

    def periodic_request(self):
        try:
            for _ in range(
                    (self.period_days * 24 * 60 * 60) // self.interval + 1):
                if self.stop_event and self.stop_event.is_set():
                    self.logger.info('Cycle stopped manually')
                    break
                temperature = self.get_temperature()
                self.write_temperature_to_file(temperature)
                self.logger.info(
                    f'Succesfully written temperature at {datetime.datetime.now()}. Next request in {self.interval} sec')
                if self.stop_event:
                    self.logger.info('Wait for next record')
                    self.stop_event.wait(self.interval)
                else:
                    self.logger.info('Go sleep')
                    time.sleep(self.interval)
        except Exception as e:
            self.logger.info(f'Error while recording temperature. {e}')


class TemperatureCycle:
    def __init__(
            self,
            lower_temp,
            higher_temp,
            logger,
            controller='WaterBathController',
            interval_hours=12,
            period_days=60,
            stop_event=None):
        self.lower_temp = lower_temp
        self.higher_temp = higher_temp
        self.curr_temp = higher_temp
        self.next_temp = lower_temp
        self.interval_hours = interval_hours
        self.controller = controller
        self.period_days = period_days
        self.logger = logger
        self.stop_event = stop_event

    def start(self):
        self.logger.info('Starting periodic temperature changes')
        self.logger.info(
            f'Succesfully start temperature cycle. Period of cycling {self.period_days} days. Interval - {self.interval_hours} hours.')
        self.cycling(self.interval_hours, self.period_days)

    def change_temp(self):
        self.curr_temp, self.next_temp = self.next_temp, self.curr_temp

    def set_controller_temperature(self, temperature):
        self.logger.info('Change controller temperature')
        self.controller.set_water_bath_temperature(temperature)

    def cycling(self, interval_hours, period_days):
        self.logger.info('Cycle start')
        try:
            for _ in range(int(period_days * 24 // interval_hours + 1)):
                if self.stop_event and self.stop_event.is_set():
                    self.logger.info('Cycle stopped manually')
                    break
                self.change_temp()
                self.logger.info(
                    f'Setting water bath temperature to {self.curr_temp}')
                self.set_controller_temperature(self.curr_temp)
                state.update_current_status(self.curr_temp, datetime.datetime.now(
                ), datetime.datetime.now()+self.self.interval_hours)
                self.logger.info(
                    f'Water bath temperature has been changed at {datetime.datetime.now()}. Next temperature cahnge in {self.interval_hours} hours')
                if self.stop_event:
                    self.stop_event.wait(60 * 60 * interval_hours)
                else:
                    time.sleep(60 * 60 * interval_hours)
        except Exception as e:
            self.logger.info(f'Error while recording temperature. {e}')


class WaterBathController:
    def __init__(self, logger, port='COM3', baudrate=4800, timeout=2.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.connection = None
        self.is_connected = False
        self.logger = logger

    def set_command(self, command):
        cmd_bytes = (command + '\r\n').encode('utf-8')
        try:
            self.connection.reset_input_buffer()
            self.connection.reset_output_buffer()
            self.connection.write(cmd_bytes)
            time.sleep(5)
            response = self.connection.readline().decode('utf-8', errors='ignore').strip()
            self.logger.info(
                f'Send commend {cmd_bytes} to water bath. Get response {response}')
            return response
        except Exception as e:
            self.logger.info(f'Error while sending command. {e}')

    def connect(self):
        try:
            self.connection = serial.Serial(port=self.port,
                                            baudrate=self.baudrate,
                                            bytesize=serial.EIGHTBITS,
                                            parity=serial.PARITY_NONE,
                                            stopbits=serial.STOPBITS_ONE,
                                            timeout=self.timeout,
                                            xonxoff=False,
                                            rtscts=False,
                                            dsrdtr=False)
            self.is_connected = True
            self.logger.info('Succesfully conneted to water bath')
        except Exception as e:
            self.logger.info(f'Error in connection. {e}')

    def temperature_transformation(self, temperature):
        temperature = '0' * (3 - len(str(temperature))) + str(temperature)
        if len(temperature) < 8:
            temperature = temperature + '0' * (5 - len(temperature))
        return temperature

    def set_water_bath_temperature(self, temperature):
        attemps = 10
        while not self.is_connected and attemps > 0:
            self.logger.info(
                f'No connection. Try to set connection. Attempts remaining {attemps}')
            self.connect()
            attemps -= 1
            time.sleep(0.1)
        if self.is_connected:
            temperature = self.temperature_transformation(temperature)
            self.set_command('S  ' + temperature)
            self.logger.info(
                f'Succesfully set water bath temperature - {temperature}')

    def get_water_bath_temperature(self):
        attemps = 10
        while not self.is_connected and attemps > 0:
            self.logger.info(
                f'No connection. Try to set connection. Attempts remaining {attemps}')
            self.connect()
            attemps -= 1
            time.sleep(0.1)
        if self.is_connected:
            temperature = self.set_command('I').split(' ')[0]
            try:
                temperature = float(temperature)
                self.logger.info(
                    f'Succesfully got water bath temperature - {float(temperature)}')
                return float(temperature)
            except Exception as e:
                self.logger.info(f'Error while obtained temperature. {e}')
                return None


def main():
    attempts = 10
    while attempts > 0:

        attempts -= 1
        try:
            logging.basicConfig(level=logging.INFO)
            logger = logging.getLogger("experiment")
            controller = WaterBathController(logger)
            lower_temp = 4
            higher_temp = 25
            tasks = [
                (
                    TemperatureCycle,
                    (lower_temp, higher_temp, logger, controller),
                    {'interval_hours': 0.2, 'period_days': 1}
                ),
                (
                    TemperatureLogger,
                    (controller, logger),
                    {'interval': 60, 'period_days': 1}
                )
            ]
            experiment = ExperimentRunner(tasks)
            experiment.start()
        except:
            continue


if __name__ == "__main__":
    main()
print('End')
v
