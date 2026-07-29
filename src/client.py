from datetime import datetime
from http import HTTPStatus
from bs4 import BeautifulSoup
from requests import Session
import logging
import random
import string
from constants import LOGIN_ENDPOINT, book_endpoint, classes_endpoint, ERROR_TAG_ID
from exceptions import BookingFailed, IncorrectCredentials, AlreadyBooked, TooManyWrongAttempts, TooEarly, MESSAGE_BOOKING_FAILED_UNKNOWN, MESSAGE_BOOKING_FAILED_NO_CREDIT, MESSAGE_BOOKING_FAILED_MAX_WAIT_CAPACITY


def _generate_fingerprint(length=50):
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choices(chars, k=length))


class AimHarderClient:

    def __init__(self, email: str, password: str, box_id: int, box_name: str):
        self.logger = logging.getLogger('aimharder-bot')
        self.box_id = box_id
        self.box_name = box_name
        print(f"🛠️ [TRAZA-CLIENTE] Inicializando cliente AimHarder para Box: {box_name} (ID: {box_id})")
        self.session = self._login(email, password, box_name)

    @staticmethod
    def _login(email: str, password: str, box_name: str):
        print(f"🔑 [TRAZA-CLIENTE] Intentando hacer login con el usuario: {email}")
        session = Session()
        session.headers.update({
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8,de;q=0.7,ca;q=0.6,it;q=0.5,pt;q=0.4,fr;q=0.3",
            "Content-Type": "application/json",
            "DNT": "1",
            "Origin": "https://login.aimharder.com",
            "Referer": "https://login.aimharder.com/",
            "Priority": "u=1, i",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        })

        response = session.post(
            "https://login.aimharder.com/api/login",
            json={
                "username": email,
                "password": password,
                "fingerprint": _generate_fingerprint(),
            },
            allow_redirects=True,
        )
        response.raise_for_status()

        # The login endpoint is on login.aimharder.com but sets amhrdrauth for domain aimharder.com.
        # requests silently drops cross-domain cookies, so we extract it manually from Set-Cookie headers.
        amhrdrauth = None
        for r in response.history + [response]:
            for cookie_header in r.headers.get("Set-Cookie", "").split("\n"):
                if "amhrdrauth=" in cookie_header:
                    amhrdrauth = cookie_header.split("amhrdrauth=")[1].split(";")[0]
                    break

        if not amhrdrauth:
            print("❌ [TRAZA-CLIENTE] Falló la extracción de la cookie 'amhrdrauth'. Credenciales incorrectas o bloqueo de Aimharder.")
            raise IncorrectCredentials
            
        print("✅ [TRAZA-CLIENTE] Login exitoso. Cookie de sesión capturada correctamente.")

        session.cookies.set("amhrdrauth", amhrdrauth, domain="aimharder.com")

        # Update session headers for subsequent requests to the gym subdomain
        session.headers.update({
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": f"https://{box_name}.aimharder.com",
            "Referer": f"https://{box_name}.aimharder.com/schedule",
            "sec-fetch-site": "same-origin",
        })

        return session

    def get_classes(self, target_day: datetime):
        endpoint = classes_endpoint(self.box_name)
        day_str = target_day.strftime("%Y%m%d")
        print(f"📡 [TRAZA-CLIENTE] Solicitando clases para el día {day_str} al endpoint: {endpoint}")
        
        response = self.session.get(
            endpoint,
            params={
                "box": self.box_id,
                "day": day_str,
                "familyId": "",
            },
        )
        
        response_json = response.json()
        bookings = response_json.get("bookings", [])
        
        print(f"📥 [TRAZA-CLIENTE] La API de Aimharder devolvió {len(bookings)} clases programadas.")
        print("   --- INICIO DEL LISTADO DE CLASES REALES (API AIMHARDER) ---")
        
        self.logger.info(f"Retrieved {len(bookings)} classes for day {target_day.strftime('%Y-%m-%d')}")
        
        for booking in bookings:
            timeid = booking.get("timeid")
            classname = booking.get("className")
            bookstate = booking.get("bookState")
            ocupation = booking.get("ocupation")
            limit = booking.get("limit")
            
            # Imprimimos cada clase por pantalla para ver exactamente cómo se llaman y qué hora tienen
            print(f"   🗓️ Hora (timeid): '{timeid}' | Nombre (className): '{classname}' | Estado: {bookstate} | Ocupación: {ocupation}/{limit}")
            
            self.logger.debug(
                "Class: id=%s timeid=%s name=%s bookState=%s ocupation=%s limit=%s",
                booking.get("id"),
                timeid,
                classname,
                bookstate,
                ocupation,
                limit,
            )
            
        print("   --- FIN DEL LISTADO DE CLASES ---")
        return bookings

    def book_class(self, target_day: datetime, target_class: str) -> bool:
        day_str = target_day.strftime("%Y%m%d")
        class_id = target_class["id"]
        
        print(f"🖱️ [TRAZA-CLIENTE] Enviando petición POST para reservar la clase ID: {class_id} el día {day_str}")
        
        response = self.session.post(
            book_endpoint(self.box_name),
            headers={
                "Origin": f"https://{self.box_name}.aimharder.com",
                "Referer": f"https://{self.box_name}.aimharder.com/schedule?cl",
                "Priority": "u=1, i",
            },
            data={
                "id": class_id,
                "day": day_str,
                "insist": 0,
                "familyId": "",
            },
        )
        
        if response.status_code == HTTPStatus.OK:
            response_json = response.json()
            print(f"📬 [TRAZA-CLIENTE] Respuesta del servidor al intentar reservar: {response_json}")
            
            if "bookState" in response_json and response_json["bookState"] == -1:
                print("❌ [TRAZA-CLIENTE] Fallo: Lista de espera superada (-1).")
                self.logger.error(f"Booking unsuccessful. Max capacity of the waiting list overpassed.")
                raise BookingFailed(MESSAGE_BOOKING_FAILED_MAX_WAIT_CAPACITY)
            
            if "bookState" in response_json and response_json["bookState"] == -2:
                print("❌ [TRAZA-CLIENTE] Fallo: Límite de créditos/sesiones alcanzado (-2).")
                self.logger.error(f"Booking unsuccessful. There is no available credits. Max number of booked sessions reached.")
                raise BookingFailed(MESSAGE_BOOKING_FAILED_NO_CREDIT)
            
            if "bookState" in response_json and response_json["bookState"] == -12:               
                if response_json.get("errorMssgLang") == "ERROR_ANTELACION_CLIENTE_HORAS":
                    print("❌ [TRAZA-CLIENTE] Fallo: Demasiado pronto para reservar (ERROR_ANTELACION_CLIENTE_HORAS).")
                    self.logger.error(f"Booking unsuccessful. Too early to book this class.")
                    raise TooEarly(target_day)
                elif response_json.get("errorMssgLang") == "NOPUEDESRESERVAMISMAHORA":
                    print("❌ [TRAZA-CLIENTE] Fallo: Intentando reservar a la misma hora dos veces (NOPUEDESRESERVAMISMAHORA).")
                    self.logger.error(f"Booking unsuccessful. You cannot book the same session twice.")
                    raise AlreadyBooked(target_day)
                else:
                    print(f"❌ [TRAZA-CLIENTE] Fallo -12 desconocido: {response_json.get('errorMssgLang')}")
                
            if "errorMssg" not in response_json and "errorMssgLang" not in response_json:
                # booking successful
                print("🎉 [TRAZA-CLIENTE] ¡Respuesta limpia! La reserva se ha confirmado.")
                self.logger.info(f"Booking completed successfully.")
                return True
            
        print(f"🚨 [TRAZA-CLIENTE] Código HTTP Inesperado ({response.status_code}) o error desconocido.")
        self.logger.error(f"UNKNOWN ERROR!!!!!.")
        raise BookingFailed(MESSAGE_BOOKING_FAILED_UNKNOWN)
    
    def cancel_booked_class(self, target_class: str) -> bool:
        print(f"🗑️ [TRAZA-CLIENTE] Intentando cancelar la clase ID: {target_class['id']}")
        response = self.session.post(
            classes_endpoint(self.box_name),
            data={
                "id": target_class["id"],
                "late": 0,
                "familyId": "",
            },
        )
        if response.status_code == HTTPStatus.OK:
            response_json = response.json()
            if "errorMssg" not in response_json and "errorMssgLang" not in response_json:
                # booking cancellation successful
                print("✅ [TRAZA-CLIENTE] Clase cancelada con éxito.")
                self.logger.info(f"Booking cancelled successfully.")
                return True
                
        print("❌ [TRAZA-CLIENTE] Error desconocido al intentar cancelar la clase.")
        self.logger.error(f"UNKNOWN ERROR!!!!!.")
        raise BookingFailed(MESSAGE_BOOKING_FAILED_UNKNOWN)
