# SURF first run

## Fastest path

### macOS / Linux

```bash
chmod +x setup.sh && ./setup.sh
source venv/bin/activate
python chat.py --web
```

### Windows PowerShell

```powershell
.\setup.ps1
.\venv\Scripts\Activate.ps1
python chat.py --web
```

Then open `http://localhost:7777`.

## What to expect

- SURF stores local runtime data in `.surf/`
- API keys can be supplied via environment variables or added in the app
- Ollama is the default provider if you want a local-first start

## If you want cloud models

Set one of these before launching:

- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `OPENROUTER_API_KEY`

You can also copy `.env.example` and use it as your own reference.

## Common friction points

- If Playwright is missing a browser, run `playwright install chromium`
- If Ollama is not running, start it with `ollama serve`
- If a provider is selected but no key is set, switch provider or add the key first
