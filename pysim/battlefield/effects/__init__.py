# Battlefield effect compilation (重构计划 §2.2 effects/).
#
# equipment.py is the live table today (static equipment modifiers consumed
# by the engine bake); technology/officer/skill/structure effects migrate
# here as B1-B3 proceed. Each module must import only stdlib + lower layers
# (no engine, no transition imports) so the engine can consume them safely.
