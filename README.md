# Calorie Tracking Discord Bot

A Discord bot that turns natural-language food logs into structured meal entries (JSON), stores them in Supabase, and lets you review/delete entries and see weekly summaries.

The bot uses Gemini to extract:
- Meal label (Breakfast/Lunch/Dinner/Snack)
- Food items + quantities
- Estimated nutrition per item (calories + macros)

Macros tracked:
- Calories
- Protein
- Carbs
- Fat
- Sugar
- Salt

It also applies a couple of special rules:
- **Oil adjustment**: if an item is described as *oily/fried/deep fried/high oil absorption*, the bot adds **+120 kcal** and **+14g fat** to that item (approx 1 tbsp oil).
- **Exact calories**: if you specify calories (example: `100cal coffee`), the bot uses that exact value.

## Features

- **AI-powered logging** via `cal!log ...`
- **Macros shown on log** (Calories + Protein/Carbs/Fat, with optional Sugar/Salt)
- **View today’s logs** (with meal IDs for easy cleanup + daily macro totals)
- **Delete by ID** to correct mistakes
- **Current week summary** grouped by day (includes macro totals)
- **All-weeks history** aggregated by week

## Commands (prefix: `cal!`)

| Command | What it does |
|---|---|
| `cal!log <food text>` | Logs a meal from natural language. |
| `cal!today` | Lists meals logged today (includes each meal `id`). |
| `cal!delete <id>` | Deletes a specific meal log by ID (only if it belongs to you). |
| `cal!week` | Shows a summary of this week’s calories grouped by day. |
| `cal!history` | Shows weekly totals across your history (currently displays up to 10 most recent weeks). |
| `cal!commands` | Shows the in-Discord help embed. |

Examples:
- `cal!log 2 eggs and toast with butter`
- `cal!log deep fried chicken breast meal`
- `cal!today`
- `cal!delete 123`
- `cal!week`

Notes:
- `cal!today`, `cal!week`, and `cal!history` depend on `created_at` being present and comparable.
- `cal!delete` depends on `id`.

## How logging works

When you run `cal!log ...`, the bot:
1. Sends your text + a system prompt to Gemini.
2. Parses Gemini’s JSON response.
3. Inserts the result into Supabase (`food_logs`).
4. Replies with an embed that lists items + calories and macros (Protein/Carbs/Fat, and optional Sugar/Salt).
