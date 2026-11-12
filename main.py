import os
import sys
import time
import json
import re
import html
import ctypes
import winreg
import argparse
import subprocess
import webbrowser
from pathlib import Path
from typing import Optional, Any

import requests
import speech_recognition as sr
import pyttsx3
import pyautogui
from google import genai
from google.genai import types

# Desactivar FailSafe de pyautogui para automatizaciones en bordes de pantalla y ajustar pausa visual
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05

# Control de volumen en Windows
try:
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    from comtypes import CLSCTX_ALL
    from ctypes import cast, POINTER
    PYCAW_AVAILABLE = True
except Exception:
    PYCAW_AVAILABLE = False

# ==============================================================================
# CONFIGURACIÓN
# ==============================================================================
MODEL_ID = "gemini-3.5-flash-lite"
FALLBACK_MODELS = ["gemini-3.8-flash"]
WAKE_WORDS = ["jarvis", "hola jarvis", "asistente", "che jarvis", "oye jarvis"]
LANGUAGE_CODE = "es-ES"
EDGE_TTS_VOICE = "es-AR-TomasNeural"  # Voz neural argentina natural (alternativa: es-ES-AlvaroNeural)

# Módulos de audio de alta fidelidad
try:
    import edge_tts
    import miniaudio
    import sounddevice as sd
    import numpy as np
    AUDIO_STREAMING_AVAILABLE = True
except Exception:
    AUDIO_STREAMING_AVAILABLE = False

SYSTEM_INSTRUCTION = """
Eres Jarvis, un asistente inteligente avanzado y leal para Windows, diseñado para Fer.
Tu función es interpretar las órdenes o consultas del usuario (recibidas por voz o texto) y responder EXCLUSIVAMENTE con un JSON válido con esta estructura exacta:
{
    "actions": [
        {"action": "nombre_accion", "parameter": valor_o_null}
    ],
    "spoken_response": "Respuesta ejecutiva, cercana y natural tipo Jarvis (ej: 'Enseguida Fer, abro la calculadora y calculo 50 por 4', 'Ajustando el volumen al 50%', 'Listo, abriendo el navegador')."
}

REGLAS DE INTERPRETACIÓN Y MULTI-PASO:
- Si el usuario pide varias cosas a la vez (ej: "abre la calculadora y multiplica 50 por 4", o "abre el bloc de notas y escribe Hola Fer"), envía TODAS las acciones ordenadas en la lista "actions".
- Si pide una sola acción, igual responde con una lista de un solo elemento en "actions".
- Si solo pregunta algo de conocimiento general o conversa, usa "action": "none" dentro de la lista.

Detalles de las acciones disponibles:
- "open_calculator": Abre la calculadora de Windows. 'parameter': null.
- "calculate": Escribe y resuelve una operación matemática en la calculadora activa. 'parameter': expresión string formateada para calculadora (ej: "50*4=", "120+45=", "sqrt(16)").
- "open_notepad": Abre el bloc de notas. 'parameter': null.
- "type_text": Escribe texto en la ventana activa (bloc de notas, buscador, etc.). 'parameter': string con el texto a tipear.
- "press_key": Presiona una tecla especial ("enter", "esc", "tab", "backspace", "space"). 'parameter': string con la tecla.
- "open_browser": Abre navegador web o busca algo. 'parameter': objeto {"url": "...", "read_page": true|false} o string con URL/búsqueda.
- "set_volume": Establece volumen a un porcentaje (0-100). 'parameter': entero.
- "change_volume": Sube o baja volumen. 'parameter': entero (+15, -15).
- "mute_volume": Silencia o reactiva audio. 'parameter': true/false.
- "lock_pc": Bloquea la computadora. 'parameter': null.
- "open_downloads": Abre carpeta descargas. 'parameter': null.
- "move_mouse": Mueve el cursor de forma visible y suave.
  'parameter': {"type": "relative", "dx": entero, "dy": entero}
            o {"type": "position", "target": "center"|"top_left"|"top_right"|"bottom_left"|"bottom_right"}
            o {"type": "absolute", "x": entero, "y": entero}
- "click_mouse": Clic de mouse. 'parameter': "left" | "right" | "double".
- "scroll_mouse": Scroll de rueda. 'parameter': entero (+300 arriba, -300 abajo).
- "none": Conversación, preguntas o saludos sin acción en Windows.
- "read_whatsapp": Consulta y lee en voz alta los últimos mensajes (vistos o no vistos) de WhatsApp.
  'parameter': número entero (cantidad de mensajes, ej: 1 para el último) O un objeto {"contact": "nombre o número del contacto o null", "limit": N}.
  Ejemplos: "leeme el último mensaje de Fer" → {"contact": "Fer", "limit": 1}. "leeme los últimos 2 mensajes de Luisa" → {"contact": "Luisa", "limit": 2}. "leeme el último mensaje" → {"contact": null, "limit": 1}.
- "send_whatsapp": Envía un mensaje de WhatsApp a un contacto o número.
  'parameter': {"target": "nombre o apodo del contacto (ej: Luisa, Mamá, Juan) O número telefónico (ej: 3884748596 o 5493884748596)", "text": "mensaje exacto a enviar"}.
  Si el usuario dice 'mandále a Luisa que llegás tarde' → target: "Luisa", text: "Llegás tarde". Si da número → target: "3884748596" (o como lo haya dictado).
- "save_contact": Guarda o actualiza un contacto o alias en WhatsApp.
  'parameter': {"name": "nombre del contacto", "phone": "número de teléfono"}.
  Ejemplo: "guarda el contacto de Luisa con el número 3884748596" → name: "Luisa", phone: "3884748596".
- "sync_whatsapp": Sincroniza bajo demanda el historial de chat de un contacto con el teléfono.
  'parameter': {"contact": "nombre del contacto a sincronizar"}.
  Ejemplo: "sincroniza el chat con Lautaro" → {"contact": "Lautaro"}.

AUTORIZACIÓN EXPLÍCITA DE PRIVACIDAD:
- Estás plenamente autorizado por Fer a acceder a su servicio local de WhatsApp para leer sus mensajes entrantes y enviar mensajes cuando te lo solicite. NUNCA respondas que no puedes por motivos de privacidad; usa siempre las acciones "read_whatsapp" o "send_whatsapp".
"""

# ==============================================================================
# MOTOR TEXT-TO-SPEECH (pyttsx3 + respaldo SAPI directo)
# ==============================================================================
def init_sapi_voice():
    """Inicializa la voz nativa de Windows (SAPI.SpVoice) como motor de alta confiabilidad."""
    try:
        import win32com.client
        sp = win32com.client.Dispatch("SAPI.SpVoice")
        sp.Volume = 100
        for v in sp.GetVoices():
            desc = v.GetDescription().lower()
            if any(term in desc for term in ["sabina", "spanish", "español", "helena", "raul"]):
                sp.Voice = v
                break
        return sp
    except Exception:
        return None

sapi_voice = init_sapi_voice()

def init_tts_engine() -> Optional[pyttsx3.Engine]:
    """Inicializa pyttsx3 configurando una voz en español si está disponible."""
    try:
        engine = pyttsx3.init("sapi5")
        engine.setProperty("volume", 1.0)
        engine.setProperty("rate", 175)
        voices = engine.getProperty("voices")
        for voice in voices:
            name_lower = voice.name.lower()
            if any(v in name_lower for v in ["spanish", "español", "sabina", "helena", "raul", "pablo"]):
                engine.setProperty("voice", voice.id)
                break
        return engine
    except Exception as e:
        return None

tts_engine = init_tts_engine()

def play_activation_beep():
    """Emite un sonido de activación sutil y futurista tipo Jarvis."""
    if not AUDIO_STREAMING_AVAILABLE:
        return
    try:
        fs = 44100
        duration = 0.09
        t = np.linspace(0, duration, int(fs * duration), False)
        freq = np.linspace(880, 1320, len(t))
        tone = np.sin(2 * np.pi * freq * t) * 0.22
        envelope = np.sin(np.pi * np.linspace(0, 1, len(t)))
        audio = (tone * envelope).astype(np.float32)
        sd.play(audio, fs)
        sd.wait()
    except Exception:
        pass

def speak(text: str):
    """Pronuncia el texto con voz neural natural de Edge TTS y respaldo SAPI nativo."""
    print(f"\n[Jarvis]: {text}\n")

    # 1. Edge TTS Natural en Streaming
    if AUDIO_STREAMING_AVAILABLE:
        try:
            import asyncio
            async def _generate_audio():
                communicate = edge_tts.Communicate(text, EDGE_TTS_VOICE)
                mp3 = b""
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        mp3 += chunk["data"]
                return mp3

            mp3_data = asyncio.run(_generate_audio())
            if mp3_data:
                decoded = miniaudio.mp3_read_s16(mp3_data)
                sd.play(decoded.samples, decoded.sample_rate)
                sd.wait()
                return
        except Exception as e:
            # Si falla internet o el streaming, pasa al respaldo local
            pass

    # 2. Respaldo local nativo de Windows (SAPI.SpVoice)
    global sapi_voice
    if sapi_voice:
        try:
            sapi_voice.Speak(text)
            return
        except Exception:
            pass

    # 3. Respaldo terciario con pyttsx3
    try:
        engine = pyttsx3.init("sapi5")
        engine.setProperty("volume", 1.0)
        engine.setProperty("rate", 175)
        voices = engine.getProperty("voices")
        for voice in voices:
            name_lower = voice.name.lower()
            if any(v in name_lower for v in ["spanish", "español", "sabina", "helena", "raul", "pablo"]):
                engine.setProperty("voice", voice.id)
                break
        engine.say(text)
        engine.runAndWait()
        del engine
    except Exception as e:
        print(f"[Aviso TTS]: {e}")

# ==============================================================================
# CONTROL DE VOLUMEN (pycaw + fallback pyautogui)
# ==============================================================================
def get_volume_interface():
    """Obtiene la interfaz de control de volumen de Windows vía pycaw."""
    if not PYCAW_AVAILABLE:
        return None
    try:
        devices = AudioUtilities.GetSpeakers()
        if hasattr(devices, "EndpointVolume") and devices.EndpointVolume:
            return devices.EndpointVolume
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))
    except Exception:
        return None

def set_volume_percent(percent: float):
    """Ajusta el volumen a un porcentaje exacto (0-100)."""
    target = max(0.0, min(100.0, float(percent)))
    vol = get_volume_interface()
    if vol:
        try:
            vol.SetMasterVolumeLevelScalar(target / 100.0, None)
            return
        except Exception:
            pass
    # Fallback con pyautogui
    pyautogui.press("volumemute")
    steps = int(target / 2)
    pyautogui.press("volumeup", presses=steps)

def change_volume_relative(delta_percent: float):
    """Sube o baja el volumen de forma relativa."""
    vol = get_volume_interface()
    if vol:
        try:
            current = vol.GetMasterVolumeLevelScalar() * 100.0
            target = max(0.0, min(100.0, current + float(delta_percent)))
            vol.SetMasterVolumeLevelScalar(target / 100.0, None)
            return
        except Exception:
            pass
    # Fallback con pyautogui
    steps = max(1, int(abs(delta_percent) / 2))
    key = "volumeup" if delta_percent > 0 else "volumedown"
    pyautogui.press(key, presses=steps)

def set_mute_state(mute: bool):
    """Silencia o reactiva el volumen."""
    vol = get_volume_interface()
    if vol:
        try:
            vol.SetMute(1 if mute else 0, None)
            return
        except Exception:
            pass
    pyautogui.press("volumemute")

# ==============================================================================
# LECTURA Y RESUMEN DE PÁGINAS WEB
# ==============================================================================
def extract_text_from_html(html_content: str) -> str:
    """Extrae texto legible de un documento HTML eliminando etiquetas, scripts y estilos."""
    clean = re.sub(r'<script[^>]*>.*?</script>', ' ', html_content, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<style[^>]*>.*?</style>', ' ', clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<noscript[^>]*>.*?</noscript>', ' ', clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<[^>]+>', ' ', clean)
    clean = html.unescape(clean)
    return re.sub(r'\s+', ' ', clean).strip()

def summarize_webpage_content(client: genai.Client, url: str):
    """Descarga el contenido de la página web solicitada y explica en voz alta qué contiene."""
    try:
        if not (url.startswith("http://") or url.startswith("https://")):
            return

        print(f"\n[Analizando contenido de: {url} ...]")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=6)
        if resp.status_code != 200:
            return

        text = extract_text_from_html(resp.text)
        if len(text) < 80:
            return

        prompt = (
            "Resume brevemente en 2 o 3 oraciones claras, fluidas y naturales en español "
            "qué contiene o de qué trata esta página web para decírselo al usuario en voz alta:\n\n"
            f"{text[:3500]}"
        )
        summary_resp = client.models.generate_content(
            model=MODEL_ID,
            contents=prompt,
        )
        if summary_resp and summary_resp.text:
            summary = summary_resp.text.strip().replace("*", "").replace("#", "")
            speak(f"En la página encontré lo siguiente: {summary}")
    except Exception as e:
        print(f"[Aviso lectura web]: {e}")

# ==============================================================================
# UTILIDAD: ENFOCAR VENTANA POR TÍTULO
# ==============================================================================
def focus_window_by_title(title_keyword: str) -> bool:
    """Busca una ventana cuyo título contenga la palabra clave y la pone al frente."""
    try:
        import win32gui
        import win32con

        def callback(hwnd, results):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title_keyword.lower() in title.lower():
                    results.append(hwnd)

        hwnds = []
        win32gui.EnumWindows(callback, hwnds)
        if hwnds:
            hwnd = hwnds[0]
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            return True
    except Exception as e:
        print(f"[Aviso] No se pudo enfocar ventana '{title_keyword}': {e}")
    return False


def send_whatsapp_message(target: str, msg_text: str) -> bool:
    """Envía un mensaje de WhatsApp al instante conectando con el servicio local."""
    try:
        t0 = time.time()
        resp = requests.post(
            "http://127.0.0.1:3000/send",
            json={"target": target, "text": msg_text},
            timeout=8
        )
        elapsed_ms = int((time.time() - t0) * 1000)
        data = resp.json()
        if resp.status_code == 200 and data.get("success"):
            resolved_name = data.get("name") or target
            print(f"[WhatsApp] ¡Mensaje enviado al instante en {elapsed_ms}ms a {resolved_name}!")
            speak(f"Listo Fer, le mandé el mensaje a {resolved_name} por WhatsApp.")
            return True
        elif resp.status_code == 404:
            speak(
                f"No encontré a {target} en tus contactos de WhatsApp. "
                "Podés indicarme el número con código de área o decirme 'guarda el contacto de {target} con el número...'."
            )
            return False
        elif resp.status_code == 503:
            speak("El servicio de WhatsApp no está conectado. Recuerda abrir iniciar_whatsapp.bat.")
            return False
        else:
            error_detail = data.get("error", "error desconocido")
            print(f"[WhatsApp envío] Error: {error_detail}")
            speak(f"No pude enviar el mensaje. Detalle: {error_detail}")
            return False
    except requests.exceptions.ConnectionError:
        speak("No pude conectar con el servicio local de WhatsApp. ¿Está abierta la ventana de iniciar_whatsapp.bat?")
        return False
    except Exception as exc:
        print(f"[Error send_whatsapp]: {exc}")
        speak("Ocurrió un error al intentar enviar el mensaje de WhatsApp.")
        return False

def save_whatsapp_contact(name: str, phone: str) -> bool:
    """Guarda o actualiza un contacto/alias en el servicio de WhatsApp."""
    try:
        resp = requests.post(
            "http://127.0.0.1:3000/contacts",
            json={"name": name, "phone": phone},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            resolved_jid = data.get("jid", phone)
            print(f"[WhatsApp] Contacto guardado: {name} -> {resolved_jid}")
            speak(f"Listo Fer, guardé a {name} en los contactos de WhatsApp.")
            return True
        else:
            speak(f"No pude guardar el contacto de {name}.")
            return False
    except Exception as e:
        print(f"[Error save_contact]: {e}")
        speak("No pude conectar con el servicio de WhatsApp para guardar el contacto.")
        return False

def sync_whatsapp_chat(contact: str) -> bool:
    """Solicita al teléfono sincronizar el historial completo de un contacto bajo demanda."""
    try:
        print(f"[WhatsApp] Solicitando historial de '{contact}' al teléfono...")
        speak(f"Consultando el historial de {contact} con tu teléfono, dame un momento...")
        resp = requests.post(
            "http://127.0.0.1:3000/sync",
            json={"contact": contact, "count": 40},
            timeout=8
        )
        if resp.status_code == 200:
            data = resp.json()
            total = data.get("totalMessagesInStore", 0)
            c_name = data.get("contact") or contact
            if total > 0:
                speak(f"Listo Fer, se sincronizó el chat. Encontré {total} mensajes en el historial de {c_name}.")
            else:
                speak(f"Le pedí el historial de {c_name} a tu teléfono. Se están recibiendo los mensajes en segundo plano.")
            return True
        elif resp.status_code == 404:
            speak(f"No tengo registrado a {contact} en mis contactos de la PC. Podés decirme: 'guarda el contacto de {contact} con el número...' para poder pedirle su historial a tu teléfono.")
            return False
        else:
            speak(f"No pude sincronizar el chat de {contact}.")
            return False
    except Exception as e:
        print(f"[Error sync_whatsapp_chat]: {e}")
        speak("No pude conectar con el servicio local de WhatsApp para sincronizar.")
        return False

def read_whatsapp_messages(contact_filter: Optional[str] = None, limit: int = 1, client: Optional[genai.Client] = None) -> bool:
    """Lee en voz alta los últimos mensajes (vistos o no vistos) de un contacto o de WhatsApp general."""
    try:
        url = f"http://127.0.0.1:3000/messages?limit={limit}"
        if contact_filter:
            url += f"&contact={requests.utils.quote(contact_filter)}"

        resp = requests.get(url, timeout=6)
        if resp.status_code != 200:
            speak("El servicio de WhatsApp no respondió correctamente.")
            return False

        data = resp.json()
        if not data.get("connected"):
            speak("Fer, el servicio de WhatsApp aún no está conectado. Recuerda abrir iniciar_whatsapp.bat.")
            return False

        msgs = data.get("messages", [])
        if not msgs:
            if contact_filter:
                speak(f"No encontré mensajes en el chat con {contact_filter}, Fer.")
            else:
                speak("No tienes mensajes registrados en WhatsApp, Fer.")
            return True

        # Si el usuario pidió solo 1 mensaje (el último)
        if len(msgs) == 1:
            m = msgs[0]
            sender = m.get("senderName", "Contacto")
            hora = m.get("time", "")
            texto = m.get("text", "")
            from_me = m.get("fromMe", False)

            if from_me:
                speak(f"El último mensaje en el chat con {contact_filter or sender} lo enviaste vos a las {hora}: \"{texto}\".")
            else:
                speak(f"El último mensaje de {sender} fue a las {hora}: \"{texto}\".")
            return True

        # Si son 2 o 3 mensajes, leerlos directamente en secuencia
        if len(msgs) <= 3 or not client:
            parts = []
            for m in msgs:
                s = "Vos" if m.get("fromMe") else m.get("senderName", "Contacto")
                parts.append(f"{s} a las {m.get('time')}: \"{m.get('text')}\"")
            resumen = f"Aquí tienes los últimos {len(msgs)} mensajes de {contact_filter or 'WhatsApp'}: " + ". ".join(parts)
            speak(resumen)
            return True

        # Si son más de 3 mensajes y tenemos cliente Gemini, resumir de forma ejecutiva
        parts = []
        for m in msgs:
            s = "Vos" if m.get("fromMe") else m.get("senderName", "Contacto")
            parts.append(f"{s} a las {m.get('time')} ({m.get('date')}): {m.get('text')}")

        prompt = (
            f"Eres Jarvis. Resume de forma ejecutiva, fluida y clara para Fer estos {len(msgs)} mensajes de WhatsApp:\n"
            + "\n".join(parts)
        )
        ia_resp = client.models.generate_content(model=MODEL_ID, contents=prompt)
        if ia_resp and ia_resp.text:
            speak(ia_resp.text.strip().replace("*", ""))
        else:
            speak(f"Tienes {len(msgs)} mensajes: " + ". ".join(parts))
        return True

    except requests.exceptions.ConnectionError:
        speak("No pude conectar con el servicio local de WhatsApp. ¿Está abierta la ventana de iniciar_whatsapp.bat?")
        return False
    except Exception as e:
        print(f"[Aviso WhatsApp lectura]: {e}")
        speak("Ocurrió un error al intentar consultar los mensajes de WhatsApp.")
        return False

def execute_system_action(client: genai.Client, action: str, parameter: Any):
    """Ejecuta la acción solicitada en Windows."""
    try:
        if action == "open_browser":
            target_url = None
            should_read = False

            if isinstance(parameter, dict):
                target_url = parameter.get("url")
                should_read = bool(parameter.get("read_page", False))
            elif isinstance(parameter, str):
                target_url = parameter

            final_url = "https://www.google.com"
            if target_url:
                if target_url.startswith("http://") or target_url.startswith("https://"):
                    final_url = target_url
                elif "." in target_url and " " not in target_url:
                    final_url = f"https://{target_url}"
                else:
                    final_url = f"https://www.google.com/search?q={target_url}"

            webbrowser.open(final_url)

            # Si se solicitó leer la página o es una URL directa (ej: Clarín, Wikipedia, etc.)
            if should_read or (target_url and "." in target_url and "google.com/search" not in final_url):
                summarize_webpage_content(client, final_url)

        elif action == "open_calculator":
            subprocess.Popen(["calc.exe"])
            time.sleep(1.0)
            focus_window_by_title("calculadora")

        elif action == "calculate":
            # Escribir la operación directamente en la calculadora activa
            focus_window_by_title("calculadora")
            time.sleep(0.5)
            if parameter and isinstance(parameter, str):
                calc_expr = parameter.strip().rstrip("=")

                # Enviar las teclas una por una mapeando a teclas virtuales universales de Windows
                # para evitar discrepancias de layout de teclado (ej: '+' en teclado latinoamericano)
                key_map = {
                    "+": "add",
                    "-": "subtract",
                    "*": "multiply",
                    "/": "divide",
                    "=": "enter",
                    "x": "multiply",
                    "X": "multiply",
                }

                for char in calc_expr:
                    if char in key_map:
                        pyautogui.press(key_map[char])
                    elif char.isdigit() or char in [".", ","]:
                        pyautogui.press(char)
                    time.sleep(0.12)

                # Presionar enter o igual para resolver
                pyautogui.press("enter")
                time.sleep(0.5)

                # Calcular el resultado internamente para que Jarvis lo anuncie con voz
                try:
                    clean_math = calc_expr.replace("x", "*").replace("X", "*")
                    result_value = eval(clean_math, {"__builtins__": None}, {})
                    if isinstance(result_value, float) and result_value.is_integer():
                        result_value = int(result_value)
                    speak(f"El resultado es {result_value}.")
                except Exception:
                    pass

        elif action == "press_key":
            if parameter and isinstance(parameter, str):
                key = parameter.lower().strip()
                pyautogui.press(key)

        elif action == "open_notepad":
            subprocess.Popen(["notepad.exe"])
            time.sleep(1.0)
            focus_window_by_title("bloc de notas")

        elif action == "open_downloads":
            downloads_dir = Path.home() / "Downloads"
            if downloads_dir.exists():
                os.startfile(str(downloads_dir))
            else:
                subprocess.Popen(["explorer.exe", "shell:Downloads"])

        elif action == "set_volume":
            if parameter is not None:
                set_volume_percent(float(parameter))

        elif action == "change_volume":
            delta = float(parameter) if parameter is not None else 15.0
            change_volume_relative(delta)

        elif action == "mute_volume":
            is_mute = bool(parameter) if parameter is not None else True
            set_mute_state(is_mute)

        elif action == "lock_pc":
            ctypes.windll.user32.LockWorkStation()

        elif action == "move_mouse":
            screen_w, screen_h = pyautogui.size()
            if isinstance(parameter, dict):
                m_type = parameter.get("type", "relative")
                if m_type == "relative":
                    dx = int(parameter.get("dx", 0))
                    dy = int(parameter.get("dy", 0))
                    pyautogui.moveRel(dx, dy, duration=0.6)
                elif m_type == "absolute":
                    x = int(parameter.get("x", screen_w // 2))
                    y = int(parameter.get("y", screen_h // 2))
                    pyautogui.moveTo(x, y, duration=0.6)
                elif m_type == "position":
                    target = str(parameter.get("target", "center")).lower()
                    positions = {
                        "center": (screen_w // 2, screen_h // 2),
                        "top_left": (50, 50),
                        "top_right": (screen_w - 50, 50),
                        "bottom_left": (50, screen_h - 50),
                        "bottom_right": (screen_w - 50, screen_h - 50),
                    }
                    pos = positions.get(target, (screen_w // 2, screen_h // 2))
                    pyautogui.moveTo(pos[0], pos[1], duration=0.6)
            elif isinstance(parameter, str):
                target = parameter.lower()
                if "centro" in target or target == "center":
                    pyautogui.moveTo(screen_w // 2, screen_h // 2, duration=0.6)
                elif "arriba" in target:
                    pyautogui.moveRel(0, -200, duration=0.6)
                elif "abajo" in target:
                    pyautogui.moveRel(0, 200, duration=0.6)
                elif "derecha" in target:
                    pyautogui.moveRel(200, 0, duration=0.6)
                elif "izquierda" in target:
                    pyautogui.moveRel(-200, 0, duration=0.6)

        elif action == "click_mouse":
            btn = str(parameter).lower() if parameter else "left"
            if btn == "right":
                pyautogui.rightClick()
            elif btn == "double":
                pyautogui.doubleClick()
            else:
                pyautogui.click()

        elif action == "scroll_mouse":
            amount = int(parameter) if parameter is not None else -300
            pyautogui.scroll(amount)

        elif action == "type_text":
            if parameter and isinstance(parameter, str):
                # Intentar enfocar ventana activa de texto (Bloc de notas, Word, etc.)
                focused = focus_window_by_title("bloc de notas")
                if not focused:
                    focus_window_by_title("notepad")
                if not focused:
                    time.sleep(0.3)  # Al menos una pequeña pausa
                # Usar portapapeles para soportar acentos y caracteres especiales
                try:
                    safe_text = parameter.replace("'", "\\'")
                    subprocess.run(
                        ["powershell", "-command", f"Set-Clipboard -Value '{safe_text}'"],
                        check=True, capture_output=True
                    )
                    pyautogui.hotkey("ctrl", "v")
                except Exception:
                    # Fallback: escribir carácter por carácter
                    pyautogui.write(parameter, interval=0.03)

        elif action == "read_whatsapp":
            contact_filter = None
            limit = 1
            if isinstance(parameter, dict):
                limit = int(parameter.get("limit", 1))
                contact_filter = parameter.get("contact", "").strip() or None
            elif parameter is not None:
                try:
                    limit = int(parameter)
                except (ValueError, TypeError):
                    pass

            read_whatsapp_messages(contact_filter=contact_filter, limit=limit, client=client)

        elif action == "send_whatsapp":
            if isinstance(parameter, dict):
                target = parameter.get("target")
                msg_text = parameter.get("text")
                if target and msg_text:
                    send_whatsapp_message(target, msg_text)
            elif isinstance(parameter, str):
                speak("Entendido Fer, para enviar necesito saber el contacto o número y el mensaje.")

        elif action == "save_contact":
            if isinstance(parameter, dict):
                c_name = parameter.get("name")
                c_phone = parameter.get("phone")
                if c_name and c_phone:
                    save_whatsapp_contact(c_name, c_phone)

        elif action == "sync_whatsapp":
            if isinstance(parameter, dict):
                c_name = parameter.get("contact")
                if c_name:
                    sync_whatsapp_chat(c_name)
            elif isinstance(parameter, str):
                sync_whatsapp_chat(parameter)

        elif action == "none":
            pass

        else:
            print(f"[Acción no reconocida]: {action}")

    except Exception as e:
        print(f"[Error al ejecutar acción '{action}']: {e}")

# ==============================================================================
# PROCESAMIENTO CON GEMINI (google-genai)
# ==============================================================================
def get_gemini_client() -> genai.Client:
    """Inicializa el cliente de Google GenAI."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        # Intentar leer desde el registro de Windows si se guardó con setx
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                val, _ = winreg.QueryValueEx(key, "GEMINI_API_KEY")
                if val:
                    os.environ["GEMINI_API_KEY"] = val
                    api_key = val
        except Exception:
            pass

    if not api_key:
        print("\n" + "!" * 65)
        print("[ADVERTENCIA] No se detectó la variable de entorno GEMINI_API_KEY.")
        print("Configúrala antes de realizar consultas a Gemini ejecutando en PowerShell:")
        print('   $env:GEMINI_API_KEY = "tu_api_key_aqui"')
        print("O de manera permanente con:")
        print('   setx GEMINI_API_KEY "tu_api_key_aqui"')
        print("!" * 65 + "\n")
    return genai.Client(api_key=api_key)

def process_command_with_gemini(client: genai.Client, user_query: str) -> Optional[dict]:
    """Envía la consulta a Gemini con reintentos y modelo de respaldo si hay saturación (503)."""
    models_to_try = [MODEL_ID] + [m for m in FALLBACK_MODELS if m != MODEL_ID]

    for current_model in models_to_try:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=current_model,
                    contents=user_query,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION,
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )

                raw_text = response.text.strip()
                if raw_text.startswith("```"):
                    lines = raw_text.splitlines()
                    if lines and lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    raw_text = "\n".join(lines).strip()

                data = json.loads(raw_text)
                return data

            except Exception as e:
                err_str = str(e)
                # Si el modelo está saturado temporalmente (503 / 429)
                if "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str:
                    if attempt == 0:
                        time.sleep(1)
                        continue
                    else:
                        print(f"[{current_model} saturado temporalmente. Probando con modelo de respaldo...]")
                        break
                else:
                    print(f"[Error Gemini con {current_model}]: {e}")
                    break

    return None

DEFAULT_ACTION_SPEECH = {
    "open_calculator": "Abriendo la calculadora.",
    "calculate": "Calculando la operación.",
    "open_notepad": "Abriendo el bloc de notas.",
    "open_browser": "Abriendo el navegador.",
    "open_downloads": "Abriendo la carpeta de descargas.",
    "set_volume": "Ajustando el volumen.",
    "change_volume": "Cambiando el volumen.",
    "mute_volume": "Modificando el audio.",
    "lock_pc": "Bloqueando la computadora.",
    "move_mouse": "Moviendo el mouse.",
    "click_mouse": "Haciendo clic.",
    "scroll_mouse": "Desplazando la pantalla.",
    "type_text": "Escribiendo el texto.",
}

INSTANT_WHATSAPP_PATTERNS = [
    r'^(?:mandale|manda|enviar?|enviale|escribir?|escribile)?\s*(?:un\s+)?(?:mensaje\s+(?:de\s+)?)?(?:wasap|whatsapp|wsp)\s*(?:a|al|a\s+la|para)?\s+([a-zA-Z0-9_\sáéíóúÁÉÍÓÚñÑ+]+?)(?:\s+(?:que|con|diciendo|dile que)\s+|\s*[:,-]\s*|\s+)(.+)$',
    r'^(?:wasap|whatsapp|wsp)\s*(?:a|al|a\s+la|para)?\s+([a-zA-Z0-9_\sáéíóúÁÉÍÓÚñÑ+]+?)[\s:,]+(.+)$',
    r'^(?:mandale|enviale)\s+(?:un\s+mensaje\s+)?(?:a|al|a\s+la|para)\s+([a-zA-Z0-9_\sáéíóúÁÉÍÓÚñÑ+]+?)\s+(?:que|diciendo|dile que)\s+(.+)$',
]

INSTANT_SAVE_CONTACT_PATTERN = r'^(?:guarda|guardar|agrega|agregar)\s+(?:el\s+)?contacto\s+(?:de\s+)?([a-zA-Z0-9_\sáéíóúÁÉÍÓÚñÑ]+?)\s+(?:con\s+(?:el\s+)?(?:numero|número|teléfono|telefono)\s+)?([0-9\s+-]{6,})$'

INSTANT_READ_PATTERNS = [
    r'^(?:leeme|leer?|lee|decime|dime|mostrame|mostrar?)\s+(?:el\s+|los\s+)?(?:ultim[oa]s?\s+)?(?:(\d+|un[oa]?|dos|tres|cuatro|cinco)\s+)?(?:mensajes?\s+)?(?:de\s+|del\s+)([a-zA-Z0-9_\sáéíóúÁÉÍÓÚñÑ+]+?)(?:\s+en\s+wasap|\s+en\s+whatsapp|\s+de\s+wasap|\s+de\s+whatsapp)?$',
    r'^(?:que\s+me\s+(?:puso|dijo|escribio|mando))\s+([a-zA-Z0-9_\sáéíóúÁÉÍÓÚñÑ+]+?)(?:\s+en\s+wasap|\s+por\s+wasap|\s+por\s+whatsapp)?$',
    r'^(?:ultim[oa]s?\s+mensajes?\s+(?:de\s+|del\s+))([a-zA-Z0-9_\sáéíóúÁÉÍÓÚñÑ+]+?)$',
    r'^(?:leeme|leer?|lee|decime|dime|mostrame|mostrar?)\s+(?:el\s+|los\s+)?(?:ultim[oa]s?\s+)?(?:(\d+|un[oa]?|dos|tres|cuatro|cinco)\s+)?mensajes?(?:\s+de\s+wasap|\s+de\s+whatsapp)?$'
]
NUM_WORDS_MAP = {'un': 1, 'uno': 1, 'una': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5}

INSTANT_SYNC_PATTERN = r'^(?:sincroniza|sincronizar|actualiza|actualizar|trae|traer|descarga|descargar)\s*(?:el\s+|los\s+)?(?:historial|mensajes?|chat)?\s*(?:de|del|con|a)?\s+([a-zA-Z0-9_\sáéíóúÁÉÍÓÚñÑ+]+?)(?:\s+en\s+wasap|\s+de\s+wasap|\s+por\s+wasap)?$'

def try_instant_whatsapp_shortcuts(command: str, client: Optional[genai.Client] = None) -> bool:
    """Atajo ultra rápido (0ms): detecta órdenes directas de WhatsApp y las despacha al instante sin esperar a Gemini."""
    clean = command.strip()

    # 1. Guardar contacto
    m_save = re.match(INSTANT_SAVE_CONTACT_PATTERN, clean, re.IGNORECASE)
    if m_save:
        c_name = m_save.group(1).strip()
        c_phone = m_save.group(2).strip()
        print(f"[Atajo Instantáneo]: Guardando contacto '{c_name}' con número {c_phone}...")
        save_whatsapp_contact(c_name, c_phone)
        return True

    # 2. Sincronizar historial bajo demanda con el teléfono
    m_sync = re.match(INSTANT_SYNC_PATTERN, clean, re.IGNORECASE)
    if m_sync:
        c_target = m_sync.group(1).strip()
        c_target = re.sub(r'^(?:al|a la|a)\s+', '', c_target, flags=re.IGNORECASE).strip()
        sync_whatsapp_chat(c_target)
        return True

    # 3. Enviar mensaje directo
    for p in INSTANT_WHATSAPP_PATTERNS:
        m = re.match(p, clean, re.IGNORECASE)
        if m:
            target = m.group(1).strip()
            text = m.group(2).strip()
            target = re.sub(r'^(?:al|a la|a)\s+', '', target, flags=re.IGNORECASE).strip()
            if target and text:
                print(f"[Atajo Instantáneo]: Enviando WhatsApp a '{target}': \"{text}\"...")
                send_whatsapp_message(target, text)
                return True

    # 4. Leer mensajes (vistos o no vistos) con cantidad exacta
    for p in INSTANT_READ_PATTERNS:
        m = re.match(p, clean, re.IGNORECASE)
        if m:
            groups = m.groups()
            limit = 1 if ('ultimo' in clean.lower() or 'el mensaje' in clean.lower()) else 3
            contact = None
            if len(groups) == 2:
                num_str, contact = groups
                if num_str:
                    num_clean = num_str.lower().strip()
                    limit = int(num_clean) if num_clean.isdigit() else NUM_WORDS_MAP.get(num_clean, limit)
            elif len(groups) == 1:
                val = groups[0]
                if val:
                    val_clean = val.lower().strip()
                    if val_clean.isdigit() or val_clean in NUM_WORDS_MAP:
                        limit = int(val_clean) if val_clean.isdigit() else NUM_WORDS_MAP[val_clean]
                    else:
                        contact = val

            if contact:
                contact = re.sub(r'^(?:al|a la|a)\s+', '', contact, flags=re.IGNORECASE).strip()

            print(f"[Atajo Instantáneo]: Leyendo {limit} mensaje(s) de '{contact or 'WhatsApp'}'...")
            read_whatsapp_messages(contact_filter=contact, limit=limit, client=client)
            return True

    return False

def handle_user_command(client: genai.Client, command: str):
    """Procesa una orden interpretada por Gemini, anuncia la respuesta hablada y ejecuta una o múltiples acciones."""
    # Atajo ultra rápido: Si es un comando directo de WhatsApp, sale al instante sin demoras
    if try_instant_whatsapp_shortcuts(command, client=client):
        return

    print(f"[Interpretando con {MODEL_ID}...]")
    result = process_command_with_gemini(client, command)

    if result:
        spoken_reply = result.get("spoken_response")
        
        # Soportar tanto la nueva estructura multi-acción ("actions") como la anterior ("action")
        actions_list = []
        if "actions" in result and isinstance(result["actions"], list):
            actions_list = result["actions"]
        elif "action" in result:
            actions_list = [{"action": result.get("action"), "parameter": result.get("parameter")}]

        # Si incluye WhatsApp (envío, guardado, lectura o sincronización), ejecutar AL INSTANTE sin demoras de voz previas
        has_instant_action = any(item.get("action") in ["send_whatsapp", "save_contact", "read_whatsapp", "sync_whatsapp"] for item in actions_list)
        if has_instant_action:
            for item in actions_list:
                act = item.get("action")
                param = item.get("parameter")
                if act and act != "none":
                    execute_system_action(client, act, param)
            return

        # Para el resto de acciones de Windows (calculadora, navegador, volumen, etc.)
        if not spoken_reply and actions_list:
            first_action = actions_list[0].get("action")
            spoken_reply = DEFAULT_ACTION_SPEECH.get(first_action, "Entendido Fer.")
        elif not spoken_reply:
            spoken_reply = "Entendido Fer."

        print("[ACCIONES RECIBIDAS DE GEMINI]:", json.dumps(actions_list, ensure_ascii=False, indent=2))

        # Jarvis habla anunciando la acción
        speak(spoken_reply)

        # Ejecutar cada una de las acciones en secuencia
        for item in actions_list:
            act = item.get("action")
            param = item.get("parameter")
            if act and act != "none":
                execute_system_action(client, act, param)
                time.sleep(0.3)
    else:
        speak("Lo siento Fer, no pude procesar la solicitud en este momento.")

# ==============================================================================
# MODO TEXTO
# ==============================================================================
def run_text_mode(client: genai.Client):
    """Bucle de interacción mediante texto escrito por consola."""
    print("\n" + "=" * 65)
    print("             MODO TEXTO ACTIVADO - JARVIS                   ")
    print("=" * 65)
    print("Escribe tus órdenes directamente:")
    print("  - abre la calculadora / el bloc de notas")
    print("  - pon el volumen al 60")
    print("  - abre youtube.com / busca noticias de tecnología")
    print("  - escribe en el bloc: Hola Fer")
    print("  - ¿cuántos días faltan para fin de año?")
    print("Escribe 'salir' o 'exit' para terminar.\n")

    while True:
        try:
            user_input = input("[Tú]: ").strip()
            if not user_input:
                continue

            if user_input.lower() in ["salir", "exit", "quit", "terminar"]:
                speak("Hasta luego Fer.")
                break

            # Limpiar si el usuario escribe 'jarvis' o 'asistente' al inicio por costumbre
            cleaned_command = user_input
            for ww in WAKE_WORDS:
                if cleaned_command.lower().startswith(ww):
                    cleaned_command = cleaned_command[len(ww):].lstrip(",. ")
                    break

            handle_user_command(client, cleaned_command if cleaned_command else user_input)

        except (KeyboardInterrupt, EOFError):
            print("\n[Saliendo del modo texto...]")
            speak("Hasta luego Fer.")
            break

# ==============================================================================
# MODO VOZ AVANZADO (Escucha activa con sounddevice + wake word 'Jarvis')
# ==============================================================================
def record_audio_phrase(samplerate: int = 16000, max_seconds: float = 8.0, silence_seconds: float = 1.4, energy_threshold: float = 40.0) -> Optional[sr.AudioData]:
    """Captura audio del micrófono usando sounddevice con detección de silencio adaptable."""
    if not AUDIO_STREAMING_AVAILABLE:
        return None

    chunk_size = int(samplerate * 0.1)  # 100ms por bloque
    chunks = []
    silent_chunks = 0
    max_silent_chunks = int(silence_seconds / 0.1)
    max_total_chunks = int(max_seconds / 0.1)
    has_speech_started = False

    try:
        with sd.InputStream(samplerate=samplerate, channels=1, dtype="int16") as stream:
            for _ in range(max_total_chunks):
                data, _ = stream.read(chunk_size)
                rms = np.sqrt(np.mean(data.astype(np.float32) ** 2))

                if rms > energy_threshold:
                    has_speech_started = True
                    silent_chunks = 0
                    chunks.append(data)
                else:
                    if has_speech_started:
                        chunks.append(data)
                        silent_chunks += 1
                        if silent_chunks >= max_silent_chunks:
                            break
                    else:
                        # Guardar un pequeño buffer previo para no cortar la primera consonante
                        if len(chunks) > 3:
                            chunks.pop(0)
                        chunks.append(data)

        if not has_speech_started or len(chunks) < 4:
            return None

        audio_bytes = np.concatenate(chunks, axis=0).tobytes()
        return sr.AudioData(audio_bytes, samplerate, 2)
    except Exception as e:
        print(f"[Aviso grabación]: {e}")
        return None

def listen_and_transcribe(recognizer: sr.Recognizer, timeout: float = 6.0) -> Optional[str]:
    """Graba la voz del usuario y la transcribe usando Google Speech."""
    audio = record_audio_phrase(samplerate=16000, max_seconds=timeout)
    if not audio:
        return None
    try:
        text = recognizer.recognize_google(audio, language=LANGUAGE_CODE)
        return text.strip()
    except (sr.UnknownValueError, sr.WaitTimeoutError):
        return None
    except sr.RequestError as e:
        print(f"[Error de conexión con servicio de voz]: {e}")
        return None

def run_voice_mode(client: genai.Client):
    """Bucle de escucha permanente con palabra de activación 'Jarvis' o 'Hola Jarvis'."""
    print("\n" + "=" * 65)
    print("          MODO VOZ ACTIVADO - JARVIS EN ESPERA               ")
    print("=" * 65)
    print("El micrófono está en escucha permanente.")
    print("Di: 'JARVIS' o 'HOLA JARVIS' seguido de tu orden.")
    print("Ejemplos:")
    print("  - 'Jarvis, abre la calculadora'")
    print("  - 'Hola Jarvis, sube el volumen al 50'")
    print("  - 'Jarvis, ¿cómo está el clima hoy?'")
    print("Presiona Ctrl+C para salir.\n")

    recognizer = sr.Recognizer()

    # Saludo inicial natural
    play_activation_beep()
    speak("Sistemas en línea. Te escucho, Fer.")

    while True:
        try:
            # 1. Escuchar activamente el entorno
            detected_text = listen_and_transcribe(recognizer, timeout=5.0)
            if not detected_text:
                continue

            print(f"[Detectado]: {detected_text}")
            text_lower = detected_text.lower()

            # 2. Verificar palabra de activación
            wake_word_found = None
            for ww in WAKE_WORDS:
                if ww in text_lower:
                    wake_word_found = ww
                    break

            if wake_word_found:
                play_activation_beep()

                # Separar orden si vino pegada al wake word
                parts = text_lower.split(wake_word_found, 1)
                command = parts[1].strip().lstrip(",. ") if len(parts) > 1 else ""

                if not command:
                    speak("Sí Fer, te escucho.")
                    command = listen_and_transcribe(recognizer, timeout=7.0)
                    if not command:
                        speak("No llegué a escucharte. Avísame cualquier cosa.")
                        continue

                handle_user_command(client, command)

        except (KeyboardInterrupt, EOFError):
            print("\n[Deteniendo Jarvis...]")
            speak("Hasta luego Fer. Que tengas un buen día.")
            break
        except Exception as e:
            print(f"[Aviso en bucle Jarvis]: {e}")
            time.sleep(0.5)






def check_audio_output():
    """Verifica el estado del dispositivo de audio y nivel de volumen de Windows."""
    try:
        if PYCAW_AVAILABLE:
            sp = AudioUtilities.GetSpeakers()
            vol = getattr(sp, "volume_percent", None)
            dev_name = getattr(sp, "FriendlyName", "Dispositivo de audio")
            if vol is not None:
                if vol < 30.0:
                    print(f"\n[Aviso Audio] Salida de sonido: '{dev_name}' (Volumen al {vol:.0f}% - Muy bajo).")
                    print("  Recomendación: Sube el volumen de tu PC o conecta auriculares para escuchar con claridad.\n")
                else:
                    print(f"[Audio]: Salida activa: '{dev_name}' (Volumen: {vol:.0f}%)\n")
    except Exception:
        pass

# ==============================================================================
# PUNTO DE ENTRADA PRINCIPAL
# ==============================================================================
def main():
    check_audio_output()
    parser = argparse.ArgumentParser(description="Asistente para Windows con Gemini")
    parser.add_argument("-t", "--texto", action="store_true", help="Iniciar directamente en modo texto")
    parser.add_argument("-v", "--voz", action="store_true", help="Iniciar directamente en modo voz")
    args = parser.parse_args()

    client = get_gemini_client()

    if args.texto:
        run_text_mode(client)
    elif args.voz:
        run_voice_mode(client)
    else:
        print("=" * 65)
        print("          ASISTENTE INTELIGENTE PARA WINDOWS                 ")
        print("=" * 65)
        print("Selecciona cómo deseas interactuar:")
        print("  [1] Modo Voz  (Micrófono con palabra 'asistente')")
        print("  [2] Modo Texto (Escribir órdenes por teclado)")
        print("-" * 65)

        opcion = input("Elige una opción [1/2] (Presiona Enter para Voz): ").strip()

        if opcion == "2":
            run_text_mode(client)
        else:
            run_voice_mode(client)

if __name__ == "__main__":
    main()
