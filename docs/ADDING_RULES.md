# Adding filename designators and splits

Only edit `src/canedge_uploader/rules.py`. A typical change takes three steps:

1. Change `requires_decoded_data()` to return `True`.
2. Read one or more signals with `signal_samples(decoded, "SignalName")`.
3. Return one or more `Segment` objects.

After changing rules, run a local real-data check before uploading:

```powershell
canedge-uploader process CANEDGE_OUTPUT --output decoded_output
```

## One complete file with multiple designators

This example labels a log `STATIC` when at least 95% of speed samples are below 0.5, and independently labels it `CHARGING` when any charger-enable sample is nonzero:

```python
def requires_decoded_data() -> bool:
    return True


def build_segments(decoded: "MDF | None", context: RecordingContext) -> list[Segment]:
    assert decoded is not None
    _, speed = signal_samples(decoded, "VehicleSpeed")
    _, charging = signal_samples(decoded, "ChargerEnable")

    labels = []
    if len(speed) and (abs(speed) < 0.5).mean() >= 0.95:
        labels.append("STATIC")
    if len(charging) and (charging != 0).any():
        labels.append("CHARGING")
    return [Segment(designators=tuple(labels))]
```

The output can contain both words. Missing or ambiguous signals raise an error for that source and are captured in the debug log instead of silently applying a wrong label.

## Split at signal transitions

Segments use seconds relative to the original recording start. This example creates one output for each contiguous operating-mode interval:

```python
def requires_decoded_data() -> bool:
    return True


def build_segments(decoded: "MDF | None", context: RecordingContext) -> list[Segment]:
    assert decoded is not None
    timestamps, values = signal_samples(decoded, "OperatingMode")
    if not len(timestamps):
        return [Segment(designators=("NO-MODE",))]

    outputs = []
    run_start = 0
    for index in range(1, len(values)):
        if values[index] != values[run_start]:
            outputs.append(Segment(
                start=float(timestamps[run_start]),
                stop=float(timestamps[index]),
                designators=(f"MODE-{values[run_start]}",),
            ))
            run_start = index
    outputs.append(Segment(
        start=float(timestamps[run_start]),
        stop=None,
        designators=(f"MODE-{values[run_start]}",),
    ))
    return outputs
```

For production rules, usually merge very short intervals (for example, under five seconds) so signal chatter does not create hundreds of files. The deterministic identity is stable as long as the raw input, DBCs, and returned segment order remain unchanged.

## Signal discovery

asammdf accepts a short DBC signal name, `Message.Signal`, or `CAN1.Message.Signal`. Prefer the qualified form if DBCs contain duplicate short names. The Activity panel and debug log show ambiguity/missing-channel errors.

