# Maia 3 assets

This directory contains the browser Maia 3 model/runtime assets. The frontend
loads them through the Maia broker and worker; keep model loading off the board
input thread and validate returned candidates at the domain boundary.
