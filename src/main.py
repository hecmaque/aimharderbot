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
    print(f"📂 [TRAZA] Intentando cargar el archivo de configuración: ./config/{filename}")
    with open(os.path.join('./config', filename), 'r') as file:
        loaded_config = yaml.safe_load(file)

    if loaded_config is None:
        print("⚠️ [TRAZA] El archivo YAML está vacío o es nulo.")
        return []

    if isinstance(loaded_config, dict):
        print("✅ [TRAZA] Archivo YAML cargado correctamente (formato dict).")
        return [loaded_config]

    if isinstance(loaded_config, list):
        print("✅ [TRAZA] Archivo YAML cargado correctamente (formato list).")
        return [config for config in loaded_config if isinstance(config, dict)]

    return []

def create_folder_if_not_exists(folder):
    if not os.path.exists(folder):
        os.makedirs(folder)

def parse_int_config_value(name: str, value) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError(f"Configuration '{name}' is empty.")
        if not raw.isdigit():
            raise ValueError(f"Configuration '{name}' must be an integer, got '{value}'.")
        return int(raw)
    raise ValueError(f"Configuration '{name}' has invalid type {type(value).__name__}.")

def get_booking_goal(booking_goals: list[str], hours_in_advance: int) -> tuple[datetime, str, str, bool]:
    today = datetime.today()
    target_day = today + timedelta(hours=hours_in_advance)
    
    print(f"⏱️ [TRAZA] HORA ACTUAL (Ejecución): {today.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🎯 [TRAZA] HORA OBJETIVO ({hours_in_advance}h en el futuro): {target_day.strftime('%Y-%m-%d %H:%M:%S')} (Día de la semana: {target_day.strftime('%A')})")
    print(f"📋 [TRAZA] Total de Booking Goals a evaluar: {len(booking_goals)}")

    for idx, goal in enumerate(booking_goals):
        print(f"\n🔍 [TRAZA] --- EVALUANDO GOAL #{idx + 1}: '{goal}' ---")
        
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
        print(f"   [TRAZA] Día: {user_goal_day_str} | Hora: {user_goal_time_str} | Clase: {user_goal_class_name_str}")

        if len(user_goal_time_str) != 4 or not user_goal_time_str.isdigit():
            logger.error(f"Invalid time in booking goal: {goal}")
            continue

        hour = int(user_goal_time_str[:2])
        minute = int(user_goal_time_str[2:])
        
        target_day_name = target_day.strftime("%A").lower()
        print(f"   [TRAZA] ¿Coincide el día de la semana? Objetivo={target_day_name}, Goal={user_goal_day_str.lower()}")
        
        if target_day_name != user_goal_day_str.lower():
            print(f"   🚫 [TRAZA] Descartado: El día no coincide.")
            continue

        class_datetime = datetime(
            target_day.year,
            target_day.month,
            target_day.day,
            hour,
            minute,
        )
        print(f"   [TRAZA] class_datetime montada: {class_datetime.strftime('%Y-%m-%d %H:%M:%S')}")

        diff = class_datetime - today
        diff_hours = diff.days * 24 + diff.seconds // 3600
        
        print(f"   [TRAZA] diff.days: {diff.days}, diff.seconds: {diff.seconds}")
        print(f"   [TRAZA] diff_hours calculado: {diff_hours} (se requieren: {hours_in_advance})")

        if (diff_hours == hours_in_advance and diff.microseconds == 0) or (diff_hours < hours_in_advance):
            print(f"   ✅ [TRAZA] ¡Condición de tiempo cumplida para este GOAL! Retornando SUCCESS=True.")
            return (target_day, user_goal_time_str, user_goal_class_name_str, True)
        else:
            print(f"   ❌ [TRAZA] Condición de tiempo NO cumplida. diff_hours >= hours_in_advance. Retornando SUCCESS=False.")
            return (target_day, user_goal_time_str, user_goal_class_name_str, False)

    print("\n⚠️ [TRAZA] Se han evaluado todos los GOALS y ninguno ha encajado con la fecha/hora. Levantando excepción NoTrainingDay.")
    raise NoTrainingDay(target_day)

def get_class_to_book(classes: list[dict], target_time: str, class_name: str) -> dict:
    global current_user_for_log
    user_name = current_user_for_log
    
    if len(classes) == 0:
        logger.error(f"{user_name} - Box is closed.")
        raise BoxClosed

    if any(target_time in str(s.get("timeid", "")) for s in classes):
        logger.info(f"{user_name} - Class found for time ({target_time})")
        if "OPEN" in class_name:
            found_classes = [s for s in classes if target_time in str(s.get("timeid", ""))]
        else:
            found_classes = [s for s in classes if target_time in str(s.get("timeid", "")) and 'OPEN' not in s.get('className', '')]
    else:
        logger.error(f"{user_name} - No class found for time ({target_time})")
        raise NoBookingGoal(target_time)

    if (len(found_classes)) > 1:
        if any(class_name in s.get("className", "") for s in found_classes):
            logger.info(f"{user_name} - Class found for class name ({class_name})")
            found_classes = [s for s in found_classes if class_name in s.get("className", "")]
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
        hours_in_advance = parse_int_config_value("hours-in-advance", config["hours-in-advance"])

        if not isinstance(booking_goals, list) or not booking_goals:
            raise ValueError("Configuration 'booking-goals' must be a non-empty list of strings.")

        print(f"⚙️ [TRAZA] Configuración parseada con éxito para Box ID: {box_id}. Horas de antelación: {hours_in_advance}")

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
        logger.error(f"Error parsing configuration parameters: {e}")
        raise e

current_user_for_log = "Unknown"

def main(current_user, configuration):
    global current_user_for_log
    current_user_for_log = current_user
    
    class_day = None
    class_time = None
    class_name = None

    print(f"\n🚀 [TRAZA] Iniciando proceso principal para usuario: {current_user}")

    try:
        email, password, box_name, box_id, booking_goals, exceptions, hours_in_advance = parse_config_params(configuration)

        class_day, class_time, class_name, success = get_booking_goal(booking_goals, hours_in_advance)

        if not success:
            print(f"🛑 [TRAZA] Success es False tras evaluar get_booking_goal. Abortando ejecución.")
            logger.info(f"{current_user} - The class is not available yet or it is too late. Target date = {class_day.strftime('%Y-%m-%d')}. Class at: {class_time}")
            return
            
        print(f"✅ [TRAZA] ¡Objetivo válido encontrado! Intentando conectar con AimHarder para {class_name} a las {class_time}...")

        client = AimHarderClient(email=email, password=password, box_id=box_id, box_name=box_name)
        logger.debug(f"{current_user} - Client connected to AimHarder.")

        classes = client.get_classes(class_day)
        print(f"📥 [TRAZA] Se han recuperado {len(classes)} clases de AimHarder para el día {class_day.strftime('%Y-%m-%d')}")

        if any((class_item.get('bookState') == 1 or class_item.get('bookState') == 0) for class_item in classes):
            print(f"🚫 [TRAZA] Se detectó que el usuario ya tiene una clase reservada o está en lista de espera ese día.")
            logger.error(f"{current_user} - The target class or another class is already booked on the target day!")
            raise AlreadyBooked(class_day)

        target_class = get_class_to_book(classes, class_time, class_name)

        print(f"🖱️ [TRAZA] Procediendo a lanzar la petición de reserva para la clase seleccionada...")
        if client.book_class(class_day, target_class):
            print(f"🎉 [TRAZA] ¡RESERVA COMPLETADA CON ÉXITO!")
            logger.debug(f"{current_user} - Training booked successfully!! {class_day.strftime('%A')} - {class_day.strftime('%Y-%m-%d')} at {class_time} -  {class_name}")
        else:
            print(f"❌ [TRAZA] La petición de reserva falló o devolvió falso.")
            logger.debug(f"{current_user} - Booking of the training unsuccessful. Target day: {class_day.strftime('%Y-%m-%d')}")
            
    except BoxClosed as e:
        print("🚨 [TRAZA-EXCEPCIÓN] El Box está cerrado.")
        logger.error("The box is closed!")
    except NoTrainingDay as e:
        print("🚨 [TRAZA-EXCEPCIÓN] No hay día de entrenamiento hoy.")
        logger.error("No training day today!")
    except TooEarly as e:
        print("🚨 [TRAZA-EXCEPCIÓN] Demasiado pronto para reservar.")
        logger.error("Too early to book the class!")
    except AlreadyBooked as e:
        print("🚨 [TRAZA-EXCEPCIÓN] La clase ya estaba reservada.")
        logger.error("The class was already booked!")
    except NoBookingGoal as e:
        print("🚨 [TRAZA-EXCEPCIÓN] No se encontró el booking goal específico entre las clases recibidas.")
        logger.error("There is no booking goal!")
    except Exception as e:
        print(f"🚨 [TRAZA-EXCEPCIÓN] Error crítico: {e}")
        logger.error(f"{current_user} - {traceback.format_exc()}")
        print(traceback.format_exc())

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
