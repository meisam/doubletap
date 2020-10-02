#!/bin/python3


import os

from threading import Timer
from tapsdk import TapSDK, TapInputMode
from tapsdk.models import AirGestures
from datetime import timedelta, datetime
from asgiref.sync import async_to_sync

import asyncio
import logging
from bleak import _logger as logger


#mappings can be partially auto generated
# ex: ctrl + letter is simply
#     ctrl_tap_code + letter_tap_code
# or
#     letter_tap_code + ctrl_tap_code
#depending on which hand has the letter


# wrapper to send keys using
# https://github.com/asweigart/pyautogui
# input: a list of keys to press simultaneously
def send_key_pyautogui(key):
    pyautogui.press(key)

# wrapper to send keys using
# https://github.com/boppreh/keyboard#keyboard.send
def send_key_boppreh_keyboard(key):
    keystring = ""
    for i in range(0, (len(key)-1) ):
        keystring = keystring + key[i] + "+"

    last = len(key) - 1
    keystring = keystring + key[last]
    keyboard.send(keystring)

blank_tap = 0b00000

# internal modifiers
NUMS = "number_layer" # also includes the arrow keys
SYMS = "symbols_layer"
TFUNS = "tap_functions_layer"
FN = "function_keys"

# the overarching mapping can be generated by combining each left_map or right_map
# with each value in shared_map
left_prefix_map = {
    #blank_tap
    0b00000 : "",
    # modifiers/layers
    0b00001 : ['shift', 'win'],
    0b00010 : ['win'],
    0b00100 : ['shift'],
    0b01000 : [NUMS],
    0b10000 : ['ctrl'],
    0b10011 : [FN],
    # modifier combos
    0b01100 : ['shift', NUMS],
    0b01001 : ['shift', NUMS, 'win'],
    0b11000 : ['ctrl', NUMS], # since arrows are on the NUMS layer, we get ctrl + arrows here
    0b01010 : ['win', NUMS],
}
left_cmd_map = {
    #blank_tap
    0b00000 : "",
    # specials
    0b11111 : ['space'],
    # letters
    0b00011 : ['a'],
    0b00110 : ['t'],
    0b00101 : ['e'],
    0b10001 : ['f'],
    0b10010 : ['x'], # mapped easily for cut
    0b00111 : ['d'],
    0b01110 : ['s'],
    0b11100 : ['z'],
    0b11001 : ['r'],
    0b01111 : ['w'],
    0b11110 : ['c'],
    0b11101 : ['g'],
    0b11011 : ['v'],
    0b01011 : ['b'],
    0b10100 : ['q'],
}

left_sym_map = {
    
}

right_prefix_map = {
    #blank_tap
    0b00000 : "",
    # modifiers/layers
    0b10000 : ['shift', 'win'],
    0b01000 : ['win'],
    0b00100 : ['shift'],
    0b00010 : [SYMS],
    0b00001 : ['ctrl'],
    # modifier combos
    0b00110 : ['shift', SYMS],
    0b00101 : ['ctrl', 'shift'], # for c, v copy/paste in terminals
}

right_cmd_map = {
    #blank_tap
    0b00000 : "",
    # specials
    0b11111 : ['backspace'],
    0b00011 : ['tap'],
    0b10011 : ['esc'],
    # punctuation
    0b10010 : ['?'],
    0b10001 : [';'],
    0b01001 : ['.'],
    0b01110 : [','],
    # letters
    0b11000 : ['o'],
    0b01100 : ['i'],
    0b10100 : ['u'],
    0b11100 : ['n'],
    0b00111 : ['h'],
    0b11001 : ['p'],
    0b11110 : ['m'],
    0b01111 : ['y'],
    0b10111 : ['l'],
    0b11011 : ['j'],
    0b11010 : ['k'],
}

right_empty_map = {
    # empty - not exhaustive, but limited to "easier" taps
    0b01010 : [""],
}

right_num_map = {

}


# special dual tap macros
dual_map = {
    0b0000000000 : "Zeros-- an impossible tap",
    0b1000000001 : "Pinkeys",
    0b0100000010 : "Rings",
    0b0010000100 : "Middles",
    0b0001001000 : "Pointers",
    0b0000110000 : "Thumbs",
    0b1111111111 : "All"
}

#TODO: need to add function on startup to ensure the prefix and cmd maps don't conflict
def parseTapcode(leftcode, rightcode):
    left_prefix = left_prefix_map.get(leftcode, default = None)
    left_cmd = left_cmd_map.get(leftcode, default = None)

    right_prefix = right_prefix_map.get(rightcode, default = None)
    right_cmd = right_cmd_map.get(rightcode, default = None)

    if ( (left_prefix != None) and (right_cmd != None) ):
        # this is legal
        # return combination of both lists
        return (left_prefix + right_cmd)

    if( (right_prefix != None) and (left_cmd != None) ):
        # this is legal
        # return combination of both lists
        return (right_prefix + left_cmd)

    leftcode = leftcode<<5
    dualcode = leftcodecode | rightcode
    dual = dual_map.get(dualcode, default = None)
    if (dual != None):
        return dual

    #elsewise we found nothing
    return None


left_tap = {"name":"left", "mac":"CE:BB:BB:2E:60:99"}
right_tap = {"name":"right", "mac":"F3:64:D7:5D:8D:D1"}
taps_by_mac = {left_tap["mac"]:left_tap["name"], right_tap["mac"]:right_tap["name"]}


def reverseBits(tapcode):
    return int('{:05b}'.format(tapcode)[::-1], 2)

#tap decoding:
# - hand comes in
# - if other hand has come in, and was recent (< 5ms?)
# -     then is a two hand combo
# - else is a one hand combo

other_hand = None
other_hand_code = None
other_hand_time = None

other_hand_timer = None

def WaitTap(hand, code):
    print("("+hand+") recognized=" + str(code))
    if (hand == "left"):
        rightcode = blank_tap
        leftcode = code
    elif (hand == "right"):
        leftcode = blank_tap
        rightcode = code
    else:
        print ("Invalid hand arg")
        return None

    command = parseTapcode(leftcode, rightcode)
    print("("+hand+") recognized code =" + str(code) + " parsed to command =" +command)


#since the bleak lib insists on using synchronous callbacks instead of async promises, etc we have to get a little stupid and use threading.
# threading, callbacks, and asyncio all in one. how fun
# if its stupid, but works, it is still stupid

def DetectTap(loop, hand, tapcode):
    print("in DecodeTap")
    global other_hand
    global other_hand_code
    global other_hand_time
    global other_hand_timer

    now = datetime.now()
    if (other_hand != None) and ((now - other_hand_time) < timedelta(milliseconds=50)):
        other_hand_timer.cancel()
        print("(dual) recognized=" + str(command))

        #TODO: need to have hand detection like in WaitTap, turn into a function so we can use it in both places
        command = parseTapcode(leftcode, rightcode)
        print("("+hand+") recognized code =" + str(code) + " parsed to command =" +command)
        other_hand = None
        return

    other_hand = hand
    other_hand_code = tapcode
    other_hand_time = datetime.now()
    other_hand_timer = Timer(0.07, WaitTap, args = [hand, tapcode])
    other_hand_timer.start()

def notification_handler(sender, data):
    """Simple notification handler which prints the data received."""
    print("{0}: {1}".format(sender, data))


def OnMouseModeChange(address, identifier, mouse_mode):
    print(identifier + " changed to mode " + str(mouse_mode))

def OnTapped(loop, address, identifier, tapcode):
    if (taps_by_mac[address] == "right"):
        tapcode = reverseBits(tapcode)
    print(taps_by_mac[address] + " (" + address + ") tapped " + str(tapcode))
    DetectTap(loop, taps_by_mac[address], tapcode)

def OnTapConnected(self, identifier, name, fw):
    print(identifier + " Tap: " + str(name), " FW Version: ", fw)


def OnTapDisconnected(self, identifier):
    print(identifier + " Tap: " + identifier + " disconnected")


def OnMoused(address, identifier, vx, vy, isMouse):
    print(identifier + " mouse movement: %d, %d, %d" % (vx, vy, isMouse))



async def run(loop=None, debug=True):
    # if debug:
    #     import sys

    #     loop.set_debug(True)
    #     h = logging.StreamHandler(sys.stdout)
    #     h.setLevel(logging.WARNING)
    #     logger.addHandler(h)
    left = TapSDK(left_tap["mac"], loop)
    right = TapSDK(right_tap["mac"], loop)



    if not await left.client.connect_retrieved():
        print("Error connecting to {}".format(left_tap["mac"]))
        return None

    print("Connected to {}".format(left.client.address))

    await left.set_input_mode(TapInputMode("controller"))
    await left.register_tap_events(OnTapped)
    await left.register_mouse_events(OnMoused)

    if not await right.client.connect_retrieved():
        print("Error connecting to {}".format(right_tap["mac"]))
        return None

    print("Connected to {}".format(right.client.address))

    await right.set_input_mode(TapInputMode("controller"))
    await right.register_tap_events(OnTapped)
    await right.register_mouse_events(OnMoused)


    #TODO: could use tap.client.list_connected_taps to detect disconnects?
    while (True):
        await asyncio.sleep(100.0)

        # print("Connected to {}".format(client.client.address))
        # await client.register_raw_data_events(OnRawData)
        # await client.register_mouse_events(OnMoused)

        # logger.info("Changing to text mode")
        #await client.set_input_mode(TapInputMode("text"))
        # await asyncio.sleep(30))
        #logger.info("Changing to raw mode")
        #await client.set_input_mode(TapInputMode("raw"))

        # await client.send_vibration_sequence([100, 200, 300, 400, 500])


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run(loop, True))
