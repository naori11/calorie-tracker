import os
import json
import discord
import google.generativeai as genai
from discord.ext import commands
from supabase import create_client, Client
from dotenv import load_dotenv

# 1. Load Environment Variables
load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# 2. Setup Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 3. Setup Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

# 4. Setup Discord
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- SYSTEM PROMPT ---
# This is where we instruct the AI on how to handle your "oily" logic
SYSTEM_PROMPT = """
You are a Nutrition Assistant. Your goal is to parse natural language food logs into JSON.

RULES:
1. Identify food items, quantities, and estimate calories.
2. OIL LOGIC: If the user mentions "oily", "fried", "deep fried", or "high oil absorption", ADD a buffer of 120 calories (approx 1 tbsp oil) to that specific item.
3. BRAND LOGIC: If a specific brand is mentioned (e.g., Birch Tree, Nescafe), use known data for that brand.
4. If the user provides the calories (e.g., "100cal coffee"), use that EXACT number.

OUTPUT FORMAT (JSON ONLY):
{
  "meal_name": "Label based on time (Breakfast/Lunch/Dinner/Snack)",
  "items": [
    {"food": "Name of food", "qty": "Quantity", "calories": 0},
    {"food": "Name of food (Oil Adjusted)", "qty": "Quantity", "calories": 0}
  ],
  "total_calories": 0
}
"""

@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user}')

@bot.event
async def on_message(message):
    # Ignore bot's own messages
    if message.author == bot.user:
        return

    # Trigger: Message must start with "log:" (case insensitive)
    if message.content.lower().startswith("log:"):
        user_input = message.content[4:].strip()
        
        # UX: React to show processing
        await message.add_reaction("⏳")

        try:
            # A. CALL GEMINI
            response = model.generate_content(f"{SYSTEM_PROMPT}\n\nUSER INPUT: {user_input}")
            
            # Clean up response (sometimes models add markdown backticks)
            cleaned_json = response.text.replace('```json', '').replace('```', '').strip()
            data = json.loads(cleaned_json)

            # B. SAVE TO SUPABASE
            payload = {
                "user_name": str(message.author),
                "meal_name": data.get('meal_name', 'Snack'),
                "items": data.get('items', []),
                "total_calories": data.get('total_calories', 0)
            }
            
            # Execute insert
            supabase.table('food_logs').insert(payload).execute()

            # C. REPLY TO USER
            # Create a pretty Discord Embed
            embed = discord.Embed(
                title=f"🍽️ {payload['meal_name']} Logged",
                color=discord.Color.green()
            )
            
            item_str = ""
            for item in payload['items']:
                item_str += f"• **{item['food']}** ({item['qty']}) - {item['calories']} kcal\n"
            
            embed.add_field(name="Items", value=item_str, inline=False)
            embed.add_field(name="Total Calories", value=f"**{payload['total_calories']}**", inline=False)
            embed.set_footer(text="Saved to Supabase database")

            await message.channel.send(embed=embed)
            await message.remove_reaction("⏳", bot.user)
            await message.add_reaction("✅")

        except Exception as e:
            await message.channel.send(f"❌ Error: {str(e)}")
            print(f"Error: {e}")

# Run the bot
bot.run(DISCORD_TOKEN)