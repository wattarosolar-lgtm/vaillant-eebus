"""Energy management (HEMS): plans heating and hot water ahead.

The planner reads prices, the PV forecast, the battery and the outdoor
temperature forecast, decides per quarter hour when hot water is charged
extra and when the zones are pre-heated or lowered, and carries that out
through the write gate only. It never touches operation modes, power limits
or the compressor.
"""
