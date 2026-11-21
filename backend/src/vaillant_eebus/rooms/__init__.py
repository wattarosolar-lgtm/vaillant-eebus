"""The room control (1.4.0): radiator thermostats per room, test runs, windows and redistribution.

It decides; the write gate (write_gate.RoomGate) is the only part that sends a
setpoint to a thermostat. Nothing here reaches the heat pump.
"""
