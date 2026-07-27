import argparse
import os
import traceback
import logging
import logging.handlers as handlers
from datetime import datetime, timedelta

import yaml

from client import AimHarderClient
from exceptions import NoBookingGoal, NoTrainingDay, BoxClosed, AlreadyBooked, TooEarly

def init_logger():

    logger = logging.getLogger('aimharder-bot')
    logger.setLevel(logging.DEBUG)
    req_logger = logging.getLogger("requests")
    req_logger.setLevel(logging.DEBUG)
    url_logger = logging.getLogger("urllib3")
    url_logger.setLevel(logging.DEBUG)

    #20Mb = 20971520 bytes
    #15Mb = 15728640 bytes
    #5Mb = 5242880 bytes

    #We set the logs folder directory to be on the same folder of the execution file
    log_dir = os.path.join(os.path.normpath(os.getcwd() + os.sep), 'logs')
    log_fname = os.path.join(log_dir, 'aimharder-bot.log')

    #Create folder if it does not exist
    create_folder_if_not_exists(log_dir)

    logHandler = handlers.RotatingFileHandler(log_fname, maxBytes=5242880, backupCount=1)
    logHandler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s - %(message)s')
    logHandler.setFormatter(formatter)

    logger.addHandler(logHandler)
    req_logger.addHandler(logHandler)
    url_logger.addHandler(logHandler)
    return logger

def load_yaml_config(filename: str):
    with open(os.path.join('./config', filename), 'r') as file:
        loaded_config = yaml.safe_load(file)

    if loaded_config is None:
        return []

    if isinstance(loaded_config, dict):
        return [loaded_config]

    if isinstance(loaded_config, list):
        return [config for config in loaded_config if isinstance(config, dict)]

    return []

def create_folder_if_not_exists(folder):
    if not os.path.exists(folder):
        os.makedirs(folder)

def get_booking_goal(booking_goals: list[str], hours_in_advance: int) -> tuple[datetime, str, str, bool]:

    # today = datetime(2025,2,8,20,2,0,000000)
    today = datetime.today()
    target_day = today + timedelta(hours=hours_in_advance)

    # We iterate over the booking goals to find the one that matches the target day
    for goal in booking_goals:
        if not isinstance(goal, str) or not goal.strip():
            logger.error(f"Skipping invalid booking goal entry: {goal}")
            continue

        parts = [part.strip() for part in goal.split(',')]
        if len(parts) < 3:
            logger.error(f"Malformed booking goal: {goal}")
            continue

        user_goal_day_str = parts[0]
        user_goal_time_str = parts[1]
        user_goal_class_name_str = parts[2]

        user_goal_time_str = user_goal_time_str.zfill(4)
        if len(user_goal_time_str) != 4 or not user_goal_time_str.isdigit():
            logger.error(f"Invalid time in booking goal: {goal}")
            continue

        hour = int(user_goal_time_str[:2])
        minute = int(user_goal_time_str[2:])
        if hour not in range(24) or minute not in range(60):
            logger.error(f"Invalid time values in booking goal: {goal}")
            continue

        logger.info(f"Calculated target date: {target_day.strftime('%Y-%m-%d %H:%M:%S')}")

        if target_day.strftime("%A").lower() != user_goal_day_str.lower():
            continue

        class_datetime = datetime(
            target_day.year,
            target_day.month,
            target_day.day,
            hour,
            minute,
        )
        logger.info(f"Calculated class to book datetime: {class_datetime.strftime('%Y-%m-%d %H:%M:%S')}")

        diff = class_datetime - today
        diff_hours = diff.days * 24 + diff.seconds // 3600
        logger.info(f"Diff in hours between class datetime and now: {diff_hours} (hours-in-advance={hours_in_advance})")

        if (diff_hours == hours_in_advance and diff.microseconds == 0) or (diff_hours < hours_in_advance):
            return (target_day, user_goal_time_str, user_goal_class_name_str, True)
        else:
            return (target_day, user_goal_time_str, user_goal_class_name_str, False)

    raise NoTrainingDay(target_day)

def get_class_to_book(classes: list[dict], target_time: str, class_name: str) -> dict:
    if len(classes) == 0:
        logger.error(f"{user_name} - Box is closed.")
        raise BoxClosed

    if any(target_time in s["timeid"] for s in classes):
        logger.info(f"{user_name} - Class found for time ({target_time})")
        if "OPEN" in class_name:
            found_classes = [s for s in classes if target_time in s["timeid"]]
        else:
            found_classes = [s for s in classes if target_time in s["timeid"] and 'OPEN' not in s['className']]
    else:
        logger.error(f"{user_name} - No class found for time ({target_time})")
        raise NoBookingGoal(target_time)

    if (len(found_classes)) > 1:
        if any(class_name in s["className"] for s in found_classes):
            logger.info(f"{user_name} - Class found for class name ({class_name})")
            found_classes = [s for s in found_classes if class_name in s["className"]]
        else:
            logger.error(f"{user_name} - No class found for class name ({class_name})")
            raise NoBookingGoal(class_name)

    logger.info(f"{user_name} - Class found: {found_classes[0]}")
    return found_classes[0]

def parse_config_params(config):
    try:
        email = config["email"]
        password = config["password"]
        box_name = config["box-name"]
        box_id = config["box-id"]
        booking_goals = config["booking-goals"]
        exceptions = config.get("exceptions")
        hours_in_advance = config["hours-in-advance"]
        return (
            email,
            password,
            box_name,
            box_id,
            booking_goals,
            exceptions,
            hours_in_advance,
        )
    except Exception as e:
        logger.error(f"{user_name} - Error parsing configuration parameters: {e}")
        raise e

def main(current_user, configuration):
    class_day = None
    class_time = None
    class_name = None

    try:
        #We parse the configuration parameters
        email, password, box_name, box_id, booking_goals, exceptions, hours_in_advance = parse_config_params(configuration)

        class_day, class_time, class_name, success = get_booking_goal(booking_goals, hours_in_advance)

        if not success:
            logger.info(f"{current_user} - The class is not available yet or it is too late. Target date = {class_day.strftime('%Y-%m-%d')}. Class at: {class_time}")
            return

        #We log in into AimHarder platform
        client = AimHarderClient(email=email, password=password, box_id=box_id, box_name=box_name)
        logger.debug(f"{current_user} - Client connected to AimHarder.")

        #We fetch the classes that are scheduled for the target day
        classes = client.get_classes(class_day)

        #We check if there is already a class booked on the target day. If so, we skip the booking process.
        #bookState = 0 => class is already booked, bookState = 1 => class is booked but you are in the waiting list
        if any((class_item['bookState'] == 1 or class_item['bookState'] == 0) for class_item in classes):
            logger.error(f"{current_user} - The target class or another class is already booked on the target day!")
            raise AlreadyBooked(class_day)

        #From all the classes fetched, we select the one we want to book.
        target_class = get_class_to_book(classes, class_time, class_name)

        #We book the class.
        if client.book_class(class_day, target_class):
            logger.debug(f"{current_user} - Training booked successfully!! {class_day.strftime('%A')} - {class_day.strftime('%Y-%m-%d')} at {class_time} -  {class_name}")
        else:
            logger.debug(f"{current_user} - Booking of the training unsuccessful. Target day: {class_day.strftime('%Y-%m-%d')}")
    except BoxClosed as e:
        logger.error("The box is closed!")
    except NoTrainingDay as e:
        logger.error("No training day today!")
    except TooEarly as e:
        logger.error("Too early to book the class!")
    except AlreadyBooked as e:
        logger.error("The class was already booked!")
    except NoBookingGoal as e:
        logger.error("There is no booking goal!")
    except Exception as e:
        logger.error(f"{current_user} - {traceback.format_exc()}")
        print(traceback.format_exc())

#We set up the loggers

if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--config-filename", required=True, type=str)
    args = parser.parse_args()

    config_file = os.path.normpath(args.config_filename)
    logger = init_logger()

    for config in load_yaml_config(config_file):
        for user_name, user_config in config.items():
            try:
                main(user_name, user_config)
            except Exception as e:
                print(e)

