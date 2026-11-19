"""Read or reset the Smart EQ EVC453 coolant pump counter after pump replacement."""

import argparse
from datetime import datetime
import ctypes as ct
import os
from pathlib import Path
import sys
import time


U32 = ct.c_uint32
ISO15765 = 6
FLOW_CONTROL_FILTER = 3
FRAME_PAD = 0x40
REQUEST_ID = 0x7E4
RESPONSE_ID = 0x7EC
READ_COUNTER = bytes.fromhex("22 35 31")
DLL = Path(r"C:\Program Files (x86)\Drew Technologies, Inc\J2534\MongoosePro JLR\monpj432.dll")


class Message(ct.Structure):
    _fields_ = [
        ("protocol", U32), ("rx_status", U32), ("tx_flags", U32),
        ("timestamp", U32), ("size", U32), ("extra_index", U32),
        ("data", ct.c_ubyte * 4128),
    ]


def message(can_id, payload=b"", flags=0):
    data = can_id.to_bytes(4, "big") + payload
    result = Message(protocol=ISO15765, tx_flags=flags, size=len(data))
    result.data[:len(data)] = data
    return result


def counter_payload(data):
    if not data.startswith(RESPONSE_ID.to_bytes(4, "big")):
        return None
    payload = data[4:]
    if payload.startswith(b"\x7f\x22") and len(payload) >= 3:
        if payload[2] == 0x78:  # Response pending
            return None
        raise RuntimeError(f"N127 refused the read request: NRC 0x{payload[2]:02X}")
    if not payload.startswith(b"\x62\x35\x31"):
        return None
    if len(payload) != 7:
        raise RuntimeError(f"Unexpected response length: {payload.hex(' ')}")
    return payload[3:]


def configure_api(dll):
    pointer = ct.POINTER(U32)
    msg = ct.POINTER(Message)
    signatures = {
        "PassThruOpen": (ct.c_void_p, pointer),
        "PassThruClose": (U32,),
        "PassThruConnect": (U32, U32, U32, U32, pointer),
        "PassThruDisconnect": (U32,),
        "PassThruStartMsgFilter": (U32, U32, msg, msg, msg, pointer),
        "PassThruStopMsgFilter": (U32, U32),
        "PassThruWriteMsgs": (U32, msg, pointer, U32),
        "PassThruReadMsgs": (U32, msg, pointer, U32),
        "PassThruGetLastError": (ct.c_char_p,),
    }
    for name, args in signatures.items():
        function = getattr(dll, name)
        function.argtypes = args
        function.restype = ct.c_int32


def checked(dll, name, *args):
    status = getattr(dll, name)(*args)
    if status:
        detail = ct.create_string_buffer(256)
        dll.PassThruGetLastError(detail)
        raise RuntimeError(f"{name}: J2534 0x{status:02X} {detail.value.decode('mbcs', 'replace')}")


def read_counter(dll, baud):
    device, channel, filter_id = U32(), U32(), U32()
    checked(dll, "PassThruOpen", None, ct.byref(device))
    try:
        checked(dll, "PassThruConnect", device, ISO15765, 0, baud, ct.byref(channel))
        try:
            mask = message(0xFFFFFFFF)
            pattern = message(RESPONSE_ID)
            flow = message(REQUEST_ID)
            checked(dll, "PassThruStartMsgFilter", channel, FLOW_CONTROL_FILTER,
                    ct.byref(mask), ct.byref(pattern), ct.byref(flow), ct.byref(filter_id))
            try:
                request = message(REQUEST_ID, READ_COUNTER, FRAME_PAD)
                count = U32(1)
                checked(dll, "PassThruWriteMsgs", channel, ct.byref(request), ct.byref(count), 1000)
                if count.value != 1:
                    raise RuntimeError("The DLL did not confirm transmission.")
                end = time.monotonic() + 4
                while time.monotonic() < end:
                    reply, count = Message(), U32(1)
                    status = dll.PassThruReadMsgs(channel, ct.byref(reply), ct.byref(count), 250)
                    if count.value and 4 <= reply.size <= len(reply.data) and not reply.rx_status & 1:
                        result = counter_payload(bytes(reply.data[:reply.size]))
                        if result is not None:
                            return result
                    if status not in (0, 0x09, 0x10):  # timeout / buffer empty are normal
                        raise RuntimeError(f"PassThruReadMsgs: J2534 0x{status:02X}")
                raise RuntimeError("No response for DID 0x3531 on standard OBD CAN.")
            finally:
                dll.PassThruStopMsgFilter(channel, filter_id)
        finally:
            dll.PassThruDisconnect(channel)
    finally:
        dll.PassThruClose(device)


def self_test():
    assert ct.sizeof(Message) == 4152
    request = message(REQUEST_ID, READ_COUNTER, FRAME_PAD)
    assert bytes(request.data[:request.size]) == bytes.fromhex("00 00 07 E4 22 35 31")
    assert counter_payload(bytes.fromhex("00 00 07 EC 62 35 31 00 01 02 03")) == bytes.fromhex("00 01 02 03")
    assert counter_payload(bytes.fromhex("00 00 07 EC 7F 22 78")) is None
    assert counter_payload(bytes.fromhex("00 00 07 E4 62 35 31 00 00 00 00")) is None
    try:
        counter_payload(bytes.fromhex("00 00 07 EC 7F 22 31"))
    except RuntimeError as error:
        assert "0x31" in str(error)
    else:
        raise AssertionError("Negative response unexpectedly accepted")
    print("Offline self-test passed.")


def exchange(dll, channel, payload, positive):
    request = message(REQUEST_ID, payload, FRAME_PAD)
    count = U32(1)
    checked(dll, "PassThruWriteMsgs", channel, ct.byref(request), ct.byref(count), 1000)
    if count.value != 1:
        raise RuntimeError("The driver did not confirm transmission.")
    expires = time.monotonic() + 4
    while time.monotonic() < expires:
        reply, count = Message(), U32(1)
        status = dll.PassThruReadMsgs(channel, ct.byref(reply), ct.byref(count), 250)
        if status not in (0, 0x09, 0x10):
            raise RuntimeError(f"PassThruReadMsgs: J2534 0x{status:02X}")
        if not count.value or not 4 < reply.size <= len(reply.data) or reply.rx_status & 1:
            continue
        data = bytes(reply.data[:reply.size])
        if int.from_bytes(data[:4], "big") != RESPONSE_ID:
            continue
        body = data[4:]
        if body.startswith(bytes((0x7F, payload[0]))) and len(body) >= 3:
            if body[2] == 0x78:
                continue
            raise RuntimeError(f"Negative response from N127: {body.hex(' ').upper()}")
        if body.startswith(positive):
            return body
    raise RuntimeError(f"No response to {payload[:3].hex(' ').upper()}.")


def read(dll, channel):
    body = exchange(dll, channel, READ_COUNTER, bytes.fromhex("62 35 31"))
    if len(body) != 7:
        raise RuntimeError("Unexpected counter response length.")
    return body[3:]


def run(dll, channel):
    before = read(dll, channel)
    print(f"Before: {before.hex(' ').upper()}", flush=True)
    backup = Path.cwd() / ("pump-counter-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".txt")
    backup.write_text(f"N127 DID 3531 before reset: {before.hex(' ').upper()}\n", encoding="ascii")
    print(f"Backup: {backup}", flush=True)
    session = exchange(dll, channel, bytes.fromhex("10 C0"), bytes.fromhex("50 C0"))
    print(f"Session: {session.hex(' ').upper()}", flush=True)
    try:
        result = exchange(dll, channel, bytes.fromhex("2E 35 31 00 00 00 00"), bytes.fromhex("6E 35 31"))
        print(f"Write acknowledged: {result.hex(' ').upper()}", flush=True)
    finally:
        after = read(dll, channel)
        print(f"After: {after.hex(' ').upper()}", flush=True)
        with backup.open("a", encoding="ascii") as log:
            log.write(f"N127 DID 3531 after reset attempt: {after.hex(' ').upper()}\n")
    if not 0 <= int.from_bytes(after, "big") <= 16:
        raise RuntimeError("The readback did not confirm the reset.")


def reset_counter(dll, baud):
    device, channel, filter_id = U32(), U32(), U32()
    checked(dll, "PassThruOpen", None, ct.byref(device))
    try:
        checked(dll, "PassThruConnect", device, ISO15765, 0, baud, ct.byref(channel))
        try:
            mask, pattern, flow = map(message, (0xFFFFFFFF, RESPONSE_ID, REQUEST_ID))
            checked(dll, "PassThruStartMsgFilter", channel, FLOW_CONTROL_FILTER,
                           ct.byref(mask), ct.byref(pattern), ct.byref(flow), ct.byref(filter_id))
            try:
                run(dll, channel)
            finally:
                dll.PassThruStopMsgFilter(channel, filter_id)
        finally:
            dll.PassThruDisconnect(channel)
    finally:
        dll.PassThruClose(device)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", type=Path, default=DLL)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--check-driver", action="store_true")
    mode.add_argument("--read", action="store_true")
    mode.add_argument("--reset-after-pump-replacement", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return
    if sys.platform != "win32" or ct.sizeof(ct.c_void_p) != 4:
        raise RuntimeError("Use 32-bit Python for Windows to load monpj432.dll.")
    path = args.dll.resolve(strict=True)
    with os.add_dll_directory(str(path.parent)):
        dll = ct.WinDLL(str(path))
        configure_api(dll)
        if args.check_driver:
            print(f"DLL loaded and J2534 exports available: {path}")
            return
        if args.reset_after_pump_replacement:
            reset_counter(dll, 500000)
            return
        raw = read_counter(dll, 500000)
    print(f"UDS response: 62 35 31 {raw.hex(' ').upper()}")
    print(f"Raw big-endian uint32 counter: {int.from_bytes(raw, 'big')} (unit unconfirmed)")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        sys.exit(str(error))
