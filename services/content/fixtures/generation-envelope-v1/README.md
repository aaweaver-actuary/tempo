# C-FENCE.v1 synthetic golden

`goldens.json` contains the approved synthetic CF01 certificate, export and expected result. Serialize the export as canonical UTF-8 JSON with sorted keys, compact separators and no trailing newline to reproduce the original raw bytes. It grants no producer or source authority. Optional legal replies are intentionally omitted from the learning profile.

The validator checks internally consistent declarations and export witnesses only. It cannot establish source completeness, actual approval, source fencing, asset authority or receipt replay across calls. Those require separately owned evidence. The 64 MiB limits are bounds, not scale certification.
