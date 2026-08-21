# Kalshi Trading Backend v3.3.10

Forward Pipeline Tracer release. Upload these files to the backend repository root.

The new tracer is diagnostic only and never writes a forward-validation trade. It runs the same live pipeline in the background and exposes stage-by-stage timing via `/v33-forward-validation/trace/{trace_id}`.
