import os
import time
import board
import digitalio
import supervisor
import storage
import alarm
from adafruit_hid.mouse import Mouse
# from adafruit_debouncer import Button
from digitalio import DigitalInOut, Direction, Pull
from seeed_xiao_nrf52840 import Battery
from adafruit_ble.services.standard import BatteryService

# imports needed for bluetooth
import adafruit_ble
from adafruit_ble.advertising import Advertisement
from adafruit_ble.advertising.standard import ProvideServicesAdvertisement
from adafruit_ble.services.standard.hid import HIDService
from adafruit_ble.services.standard.device_info import DeviceInfoService



# config defaults - we load hand specific configs from the config.py
# file. Any customizations can be made in config.py so that no
# edits are required to code.py when the code is updated.
from config import config as hand_config
config = {
    'charge_current': Battery.CHARGE_50MA,
    'log_level': 'info',
    'log_to_disk': False,
    'blink_interval': 30000,        # lower to blink status more frequently
    'debounce_sleep': 0.15,         # for button debounce
    'sp_initial': 0.2,              # scroll settings
    'sp_accel': 0.015,              # scroll acceleration
    'sp_max': 0.02,                 # max scroll speed
    'movement_accel_delay': 0.15    # mouse movement acceleration delay
}
config.update(hand_config)


# Instead of sleeps, which block any other operations,  we get the
# timestamp for some time in the future, and do checks for passing that
# time to take the required follow-on actions - ex turning off LEDs
# after turning them on, handling switch debouncing, scrollwheel
# acceleration
LEDOFF_TIME = None
DEBOUNCE_TIME = None
def get_delay_time(seconds):
    return time.monotonic_ns() + (seconds * 10**9)


###########################
# Code for logging messages
###########################

# Logging Levels
LLVL={'debug': 0, 'info': 1, 'warn': 2, 'error':3}
# Set up logfile - rotate old ones.
logfile_handle = None
if config['log_to_disk']:
    try:
        storage.remount("/",readonly=False)
        if "logfile.log" in os.listdir():
            os.rename("logfile.log","logfile.log.0")
        for i in reversed(range(3)):
            if f"logfile.log.{i}" in os.listdir():
                os.rename(f"logfile.log.{i}",f"logfile.log.{i+1}")
        logfile_handle = open("logfile.log","w")
    except (OSError, RuntimeError):
        pass

def logtime():
    cur_time = time.localtime()
    return f"{cur_time.tm_hour:02d}:{cur_time.tm_min:02d}:{cur_time.tm_sec:02d}"

# function to log a message to console, and if possible
# to a file on disk
def log(level,message):
    if LLVL[level] >= LLVL[config['log_level']]:
        log_line = logtime()+" "+message
        print(log_line)
        if logfile_handle is not None:
            logfile_handle.write(log_line+"\n")
            logfile_handle.flush()

# try to capture failures - if the program crashes the backtrace
# will be logged when the program restarts.
supervisor.set_next_code_file(filename='code.py', reload_on_error=True)
backtrace = supervisor.get_previous_traceback()
if backtrace is not None:
    log('error',"Previous run crashed.. backtrace follows...")
    log('error',backtrace)


######################
# Set up initial state
######################

# set LEDs
blue_led = DigitalInOut(board.LED_BLUE)
blue_led.direction = Direction.OUTPUT
green_led = DigitalInOut(board.LED_GREEN)
green_led.direction = Direction.OUTPUT
red_led = DigitalInOut(board.LED_RED)
red_led.direction = Direction.OUTPUT
blue_led.value = True  # turn off LED
green_led.value = True  # turn off LED
red_led.value = True  # turn off LED

# Battery set
battery = Battery()
log('info',f"Charge status (True-full charged, False-otherwise): {battery.charge_status}")
log('info',f"Voltage: {battery.voltage}V")
battery.charge_current = config['charge_current']  # setting charge according to config
log('info',f"Charge current (0-50mA, 1-100mA): {battery.charge_current}")
battery_service = BatteryService()

# setup bluetooth
hid = HIDService()
device_info = DeviceInfoService(software_revision=adafruit_ble.__version__)
advertisement = ProvideServicesAdvertisement(hid)
advertisement.appearance = 961
scan_response = Advertisement()
scan_response.complete_name = config['name']
ble = adafruit_ble.BLERadio()
ble.name = config['name'] # set name after connection

# set buttons
left_BTN = digitalio.DigitalInOut(config['left_btn'])
left_BTN.direction = Direction.INPUT
left_BTN.pull = Pull.UP
right_BTN = digitalio.DigitalInOut(config['right_btn'])
right_BTN.direction = Direction.INPUT
right_BTN.pull = Pull.UP
scrollup_BTN = digitalio.DigitalInOut(config['scrollup_btn'])
scrollup_BTN.direction = Direction.INPUT
scrollup_BTN.pull = Pull.UP
scrolldown_BTN = digitalio.DigitalInOut(config['scrolldown_btn'])
scrolldown_BTN.direction = Direction.INPUT
scrolldown_BTN.pull = Pull.UP
# for mouse movement to define left click
if config['mouse_movement']:
    enter_BTN = digitalio.DigitalInOut(config['power_btn'])
    enter_BTN.direction = Direction.INPUT
    enter_BTN.pull = Pull.UP

# set mouse
mouse = Mouse(hid.devices)

# LED blink interval counter
i = -1

#scroll
scroll_sleep = config['sp_initial']

#mouse movement acceleration delay, only accelerate after keep pressing for this long
movement_accel_delay = config['movement_accel_delay']

# Deep Sleep param
start_time = None
push_time = 10 # time to push left and right mouse buttons to go sleep (10 seconds)
IDLE_TIMEOUT = 900 # idle time to activate Deep Sleep (15 minutes)
last_activity_time = time.monotonic()

##########################
# Battery related routines
##########################

def get_batt_percent(volts):
    # Returns battery capacity percent as an integer
    # from 0 to 100.
    batt_table = {
        4.26:	100,
        4.22:	95,
        4.19:	90,
        4.15:	85,
        4.11:	80,
        4.07:	75,
        4.03:	70,
        4.00:	65,
        3.96:	60,
        3.92:	55,
        3.88:	50,
        3.84:	45,
        3.80:	40,
        3.77:	35,
        3.73:	30,
        3.69:	25,
        3.65:	20,
        3.61:	15,
        3.58:	10,
        3.54:	5,
        3.50:	0
    }
    for k in sorted(batt_table.keys(), reverse = True):
        v = batt_table[k]
        if volts < k:
            # fallthrough to next higher batt level
            continue
        percent = v
        break
    return percent


def battery_leds():
    # set green/orange/red LED based on battery state/level.
    # 3.7V lithium ion battery can be considered dead (completely discharged) at a voltage of 3.4V
    volts = battery.voltage
    percent = get_batt_percent(volts)

    charge_status = battery.charge_status
    log('info',f"Voltage: {battery.voltage}V Charge: {percent}% Charging: {not charge_status}")
    #if volts > 3.7:
    if charge_status:
        green_led.value = False  # turn on LED
    elif percent > 79:
        green_led.value = False
    elif percent > 19:
        green_led.value = False
        red_led.value = False
    else:
        # below 3.5 / 20%
        red_led.value = False


# Turns off all LED's
def leds_off():
    blue_led.value = True  # reset LED status
    green_led.value = True  # reset LED status
    red_led.value = True  # reset LED status


# Deep Sleep
def enter_sleep():
    leds_off()
    time.sleep(0.2)
    red_led.value = False
    time.sleep(0.2)
    red_led.value = True
    time.sleep(0.2)
    green_led.value = False
    time.sleep(0.2)
    green_led.value = True
    time.sleep(0.2)
    blue_led.value = False
    time.sleep(0.2)
    blue_led.value = True
    # Set up a wakeup alarm
    if config['mouse_movement']:
        switch_alarm = alarm.pin.PinAlarm(pin=board.D0, value=False, edge=False, pull=True) # fake pin, not connected
    else:
        switch_alarm = alarm.pin.PinAlarm(pin=config['power_btn'], value=False, edge=False, pull=True)
    # Enter sleep until the alarm is triggered
    log('info', "Enter to Deep Sleep")
    alarm.exit_and_deep_sleep_until_alarms(switch_alarm)


###########
# MAIN LOOP
###########

while True:

    if not ble.connected:

        ble.start_advertising(advertisement, scan_response)
        log('info',"Advertising...")
        while not ble.connected:
            # Check idle timer
            if time.monotonic() - last_activity_time > IDLE_TIMEOUT:
                enter_sleep()

            log('info',"Connecting...")
            blue_led.value = False
            time.sleep(0.5)
            blue_led.value = True
            time.sleep(0.5)
            # check if mouse movement is not set. If yes then skip Deep Sleep by click
            if not config['mouse_movement']:
                # Deep Sleep by click from advertaising
                if config[('deep_sleep_by_click')]:
                    if left_BTN.value is False or right_BTN.value is False:
                        start_time = time.monotonic()   # start count button push time
                        while left_BTN.value is False or right_BTN.value is False:
                            if time.monotonic() - start_time >= push_time and (right_BTN.value is False or left_BTN.value is False):
                                enter_sleep()
                    else:
                        start_time = None

            pass
        # Now we're connected
        ble.stop_advertising()
        log('info',f"Connected {ble.connections}")

    while ble.connected:

        # Check idle timer
        if time.monotonic() - last_activity_time > IDLE_TIMEOUT:
            enter_sleep()

        # Perform status LED blinks for bluetooth/voltage
        if i == config['blink_interval'] * 2:
            blue_led.value = False
            LEDOFF_TIME = get_delay_time(0.1)
            i = -1
        elif i == config['blink_interval']:
            battery_leds()
            battery_service.level = get_batt_percent(battery.voltage)   # info from BatteryService to show icon with percentage in Windows
            LEDOFF_TIME = get_delay_time(0.1)
        elif i % 1000 == 0:
            log('debug',f"LEDOFF {LEDOFF_TIME} MONO {time.monotonic_ns()}")
        elif LEDOFF_TIME is not None and LEDOFF_TIME < time.monotonic_ns():
            log('debug',f"LightsOut {LEDOFF_TIME} MONO {time.monotonic_ns()}")
            LEDOFF_TIME = None
            leds_off()
            #i = i+1
        i = i+1

        # Handle button clicks
        if left_BTN.value is False:
            if DEBOUNCE_TIME is not None and DEBOUNCE_TIME > time.monotonic_ns():
                continue
            DEBOUNCE_TIME = get_delay_time(config['debounce_sleep'])
            # mouse.click(Mouse.LEFT_BUTTON)
            last_activity_time = time.monotonic()  # Reset the idle timer
            if config['mouse_movement']:
                mouse.move(10,0)
                log('info',"Move right")
                start_time = time.monotonic()
                while left_BTN.value is False:
                    if time.monotonic() - start_time > movement_accel_delay:
                        mouse.move(5, 0)
                start_time = None
            else:
                mouse.press(Mouse.LEFT_BUTTON)
                start_time = time.monotonic()   # start count button push time
                while left_BTN.value is False:
                    # Deep Sleep trigger
                    if config['deep_sleep_by_click']:
                        if time.monotonic() - start_time >= push_time:
                            enter_sleep()
                    pass
                mouse.release(Mouse.LEFT_BUTTON)
                start_time = None
                log('info',"Left Button is pressed")

        elif right_BTN.value is False:
            if DEBOUNCE_TIME is not None and DEBOUNCE_TIME > time.monotonic_ns():
                continue
            DEBOUNCE_TIME = get_delay_time(config['debounce_sleep'])
            # mouse.click(Mouse.RIGHT_BUTTON)
            last_activity_time = time.monotonic()  # Reset the idle timer
            if config['mouse_movement']:
                mouse.move(-10,0)
                log('info',"Move left")
                start_time = time.monotonic()
                while right_BTN.value is False:
                    if time.monotonic() - start_time > movement_accel_delay:
                        mouse.move(-5, 0)
                start_time = None
            else:
                mouse.press(Mouse.RIGHT_BUTTON)
                start_time = time.monotonic()   # start count button push time
                while right_BTN.value is False:
                    # Deep Sleep trigger
                    if config['deep_sleep_by_click']:
                        if time.monotonic() - start_time >= push_time:
                            enter_sleep()
                    pass
                mouse.release(Mouse.RIGHT_BUTTON)
                start_time = None
                log('info',"Right Button is pressed")

        elif not scrollup_BTN.value:
            last_activity_time = time.monotonic()  # Reset the idle timer
            if config['mouse_movement']:
                if DEBOUNCE_TIME is not None and DEBOUNCE_TIME > time.monotonic_ns():
                    continue
                DEBOUNCE_TIME = get_delay_time(config['debounce_sleep'])
                mouse.move(0,-10)
                log('info',"Move up")
                start_time = time.monotonic()
                while scrollup_BTN.value is False:
                    if time.monotonic() - start_time > movement_accel_delay:
                        mouse.move(0,-5)
                start_time = None
            else:
                if DEBOUNCE_TIME is not None and DEBOUNCE_TIME > time.monotonic_ns():
                    continue
                DEBOUNCE_TIME = get_delay_time(scroll_sleep)
                scroll_sleep -= config['sp_accel']
                if scroll_sleep < config['sp_max']:
                    scroll_sleep = config['sp_max']
                DEBOUNCE_TIME = get_delay_time(scroll_sleep)
                mouse.move(wheel=1)
                log('info',"Up Button is pressed")

        elif not scrolldown_BTN.value:
            last_activity_time = time.monotonic()  # Reset the idle timer
            if config['mouse_movement']:
                if DEBOUNCE_TIME is not None and DEBOUNCE_TIME > time.monotonic_ns():
                    continue
                DEBOUNCE_TIME = get_delay_time(config['debounce_sleep'])
                mouse.move(0,10)
                log('info',"Move down")
                start_time = time.monotonic()
                while scrolldown_BTN.value is False:
                    if time.monotonic() - start_time > movement_accel_delay:
                        mouse.move(0,5)
                start_time = None
            else:
                if DEBOUNCE_TIME is not None and DEBOUNCE_TIME > time.monotonic_ns():
                    continue
                DEBOUNCE_TIME = get_delay_time(scroll_sleep)
                scroll_sleep -= config['sp_accel']
                if scroll_sleep < config['sp_max']:
                    scroll_sleep = config['sp_max']
                DEBOUNCE_TIME = get_delay_time(scroll_sleep)
                mouse.move(wheel=-1)
                log('info',"Down Button is pressed")

        elif config['mouse_movement']:
            if enter_BTN.value is False:
                last_activity_time = time.monotonic()  # Reset the idle timer
                if DEBOUNCE_TIME is not None and DEBOUNCE_TIME > time.monotonic_ns():
                    continue
                DEBOUNCE_TIME = get_delay_time(config['debounce_sleep'])
                mouse.press(Mouse.LEFT_BUTTON)
                while enter_BTN.value is False:
                    pass
                mouse.release(Mouse.LEFT_BUTTON)
                log('info',"Left Button is pressed")

        else:
            if scroll_sleep != config['sp_initial']:
                log('info',"scroll_sleep reset")
                scroll_sleep = config['sp_initial']

    log('info','Not Connected (lost connection)')
