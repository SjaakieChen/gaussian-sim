# Output Examples

These JSON files show the return format for every TMPython class currently in
the migration package.

They are examples, not fixed golden values. Real output depends on the input
JSON, current machine positions, measured power, image content, and algorithm
state.

Common fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Contract version. Current value is `1`. |
| `action` | `move`, `done`, or `abort`. |
| `move_count` | Number of requested relative moves. Use `0`, `1`, or `2`. |
| `stage1` | First stage requested by Python, or empty string. |
| `distance1_um` | First relative movement in micrometres. |
| `moves` | Structured list of requested relative moves. |
| `message` | Human-readable status for logs. |
| `state` | Optional Python-owned diagnostic/algorithm state. |

YASE must validate stage names, distances, safe limits, and machine state
before executing any move.
