
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
        self.logger = logging.getLogger()
        self.logger.info(f'Experiment starting. It is {datetime.datetime.now()}')
        self.config_path = config_path
        self.code_manager = CodeManager(config_path, self.logger)
        self.experiment_manager = ExperimentManager(
            self.code_manager
        )
    def start(self):
        self.experiment_manager.start()


class CodeManager:
    def __init__(self, config, logger):
        self.logger = logger
        self.config_manager = ConfigManager(config)
        self.error_handler = ErrorHandler(self.logger, self.config_manager)
        self.thread_manager = ThreadManager(manual_interception=True)
        

    def recovery_needed(self):
        return self.config_manager.recovery_needed()

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
    def __init__(self, logger, config_manager):
        self.logger = logger
        self.config_manager = config_manager

    def mark_error_end(self):
        self.config_manager.update_file({"end_with_error": False})

    def log_error(self, method, error):
        self.logger.info(
            f"Whlie running {method} the error appeared. Try to restore experiment. Erorr - {error}")

    def message_about_error(self):
        pass

    def handle_error(self, method, error):
        self.mark_error_end()
        self.log_error(method, error)
        self.message_about_error()


class ThreadManager:
    def __init__(self):
        self.stop_event = threading.Event()

    def start(self, target, name):
        t = threading.Thread(target=target, name=name)
        t.start()
        return t

    def keyboard_listener(self):
        input("Press enter to stop...\n")
        self.stop_event.set()


class ExperimentManager:
    def __init__(self, supervisor):
        self.supervisor = supervisor
    def start(self):
        try:
            if self.supervisor.recovery_needed():
                self.supervisor.recovery()
            tasks = self.supervisor.before_start()
            self.runner = ExperimentRunner(tasks, self.supervisor)
            self.runner.start()
        except Exception as e:
            self.supervisor.handle_error("ExperimentManager.start", e)
            raise


class ExperimentRunner:
    def __init__(self, tasks, supervisor):
        self.tasks = tasks  
        self.threads = []
        self.logger = logging.getLogger("ExperimentRunner")
        self.supervisor = supervisor

    def start(self):
        try:
            self.logger.info("Experiment is starting...")
            for cls, args, kwargs in self.tasks:


                obj = cls(*args, **kwargs)

                name = getattr(obj, 'name', obj.__class__.__name__)
                self.logger.info(f"Starting task: {name}")

                t = self.supervisor.thread_manager.start(target=obj.start, name=name)
                self.threads.append(t)

            self.logger.info("All tasks have been launched.")

            for t in self.threads:
                t.join()

            self.logger.info("Experiment completed successfully.")

        except Exception as e:
            self.supervisor.error_handler.handle_error('experiment_runner start', e)


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
    def update_current_satus(self, temperature):
        current_status = {
    "current_temp": temperature,
    "current_cycle_begin": datetime.datetime.now(),
    "current_cycle_end": datetime.datetime.now()+datetime.timedelta(hours=self.interval_hours),
    "end_cycle_earlier": 'false'
  }
        self.supervisor.config_manager.update_status(current_status)
    def cycling(self, interval_hours, period_days, supervisor):
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
                self.update_current_satus(self.curr_temp)
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
            manager = main_manager("path/to/your/config.json")
            manager.start()
        except:
            continue


if __name__ == "__main__":
    main()
print('End')
