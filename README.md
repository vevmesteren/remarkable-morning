# remarkable-morning

Daily news digest pipeline for reMarkable Paper Pro. Fetches 19 sources across EN/DE/FR, curates via LLM, builds a reflowable EPUB, and uploads to reMarkable Cloud via `rmapi`.

## Status

🚧 In development — spec complete, dispatching to vevlivery.

## Spec

Full spec: [`projects/remarkable-morning/remarkable-morning-spec.md`](https://github.com/vevmesteren/veviki-valentin/blob/main/projects/remarkable-morning/remarkable-morning-spec.md) in veviki-valentin.

## Architecture

```mermaid
graph TD
    TIMER["systemd timer 05:30 EDT"] --> F1["1. Fetch RSS + Guardian API"]
    F1 --> F2["2. Extract full text (trafilatura)"]
    F2 --> F3["3. AI Curation (dedup + categorize + briefing)"]
    F3 --> F4["4. EPUB Assembly (ebooklib)"]
    F4 --> F5["5. Upload via rmapi"]
    F5 --> F6["6. Cleanup files > 7 days"]
```

## License

MIT
