# The candidate

## The seed

[`seed_capability/`](../../examples/tau2_sierra_airline/blackbox/seed_capability/) holds four
parts:

| Path | What it is | Editable |
|---|---|---|
| `my_skill/SKILL.md` | the skill's prose, empty in the seed so that it adds nothing | no (sealed) |
| `my_skill/scripts/<tool>.py` | one tool per file, 14 in the seed: tau2's airline tools as signatures + docstrings | **yes** |
| `primitive_tools/functions.py` | 14 `env_<tool>` functions, each calling the environment service for the trial's `env_id` | no (sealed) |
| `reference/data_model.py` | tau2's airline data model, read-only context for the optimizer | no (sealed) |

A seed tool is just the signature and docstring, calling its primitive:

```python
def get_user_details(user_id: str):
    """
    Get the details of a user, including their reservations.

    Args:
        user_id: The user ID, such as 'sara_doe_496'.
    ...
    """
    return env_get_user_details(user_id=user_id)
```

The 14 primitives are imported into the store as standalone tools, tagged
`tau2_airline_primitives`. That keeps them out of the skill, so they survive every deploy.
`protected_paths: ["primitive_tools/*", "my_skill/SKILL.md", "reference/*"]` seals the files:
the harness checks them after every proposal, and editing one voids the candidate.

## What the optimizer edits, and the delivery rules

The optimizer edits `my_skill/scripts/`. Its rules are in
[`optimizer/INSTRUCTIONS.md`](../../examples/tau2_sierra_airline/blackbox/optimizer/INSTRUCTIONS.md).
Breaking one breaks the tool **silently**:

1. **One public top-level `def` per file, named exactly like the file.** Every top-level `def`
   becomes a tool, so helpers are nested and `_`-prefixed.
2. **The signature and docstring are the schema the model reads.**
   - The seed's 14 signatures are frozen, because the scorer matches actions by name and
     arguments.
   - A structured parameter gets its Pydantic class copied into that tool's own file, so the store
     publishes a rich schema (enums, nested fields).
   - The `def` stays on one line, and a model can't refer to itself.
3. **The environment is reached only through `env_<tool>` primitives**, called by bare name.
4. **Never** call `_make_api_call`, edit `primitive_tools/`, or write to `my_skill/SKILL.md`.
5. **A new or composite tool is a new file**, and removing a tool means deleting its file.

## An example: what an optimized candidate looks like

The best candidate of the worked run, `cand_0003`, kept the 14 tools, rewrote most of them and
added 6 new ones. Examples:

- **`find_same_day_reservations`**, a new read-only tool. It groups a user's flights by day, so the
  agent cancels exactly the right duplicate bookings instead of juggling them by hand.
- **`book_reservations_in_order`**, a new composite tool. It books several reservations
  sequentially in a fixed order, so the reservation ids are deterministic.
- **A check in `cancel_reservation`.** When travel insurance is the only ground for a
  cancellation, it refuses and routes to the new `cancel_reservation_with_insurance(reservation_id,
  reason)`, which checks the stated reason first.

Every check returns an `"Error: ..."` string instead of acting, and the agent usually recovers
from it.
