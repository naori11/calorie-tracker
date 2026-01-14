import os
import json
import discord
import google.generativeai as genai
from discord.ext import commands
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime, timedelta
import threading

# 1. Load Environment Variables
load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
DEV_USER_IDS = os.getenv('DEV_USER_IDS', '').split(',')  # Comma-separated Discord user IDs

# 2. Setup Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 3. Setup Gemini
genai.configure(api_key=GEMINI_API_KEY)
current_model_name = 'gemini-2.5-flash-lite'  # Default model
model = genai.GenerativeModel(current_model_name)
model_lock = threading.Lock()  # Protect model variables from concurrent access

# 3.1. Dev-only check helper
def is_dev(user_id: int) -> bool:
    """Check if user is a developer"""
    return str(user_id) in DEV_USER_IDS

# 4. Setup Discord with cal! prefix
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="cal!", intents=intents)

# 5. Helper function to generate short meal ID
def generate_short_id(user_name: str, date_obj: datetime) -> str:
    """Generate a short, systematic ID like M14A (Meal on day 14, sequence A)"""
    day = date_obj.day
    
    # Get today's meals to determine sequence letter
    today_start = date_obj.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    today_end = date_obj.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
    
    result = supabase.table('food_logs')\
        .select('short_id')\
        .eq('user_name', user_name)\
        .gte('created_at', today_start)\
        .lte('created_at', today_end)\
        .order('created_at', desc=False)\
        .execute()
    
    # Calculate next sequence letter (A, B, C, ...)
    sequence_num = len(result.data) if result.data else 0
    sequence_letter = chr(65 + sequence_num)  # 65 is ASCII for 'A'
    
    # If we exceed Z, wrap to AA, AB, etc.
    if sequence_num > 25:
        first_letter = chr(65 + ((sequence_num - 26) // 26))
        second_letter = chr(65 + ((sequence_num - 26) % 26))
        sequence_letter = first_letter + second_letter
    
    return f"M{day}{sequence_letter}"

# --- SYSTEM PROMPT ---
# This is where we instruct the AI on how to handle your "oily" logic
SYSTEM_PROMPT = """
You are a Nutrition Assistant. Your goal is to parse natural language food logs into JSON with full macro information.

RULES:
1. Identify food items, quantities, and estimate calories AND macronutrients (protein, carbs, fat, sugar, salt).
2. OIL LOGIC: If the user mentions "oily", "fried", "deep fried", or "high oil absorption", ADD a buffer of 120 calories and 14g fat (approx 1 tbsp oil) to that specific item.
3. BRAND LOGIC: If a specific brand is mentioned (e.g., Birch Tree, Nescafe), use known data for that brand.
4. If the user provides the calories (e.g., "100cal coffee"), use that EXACT number and estimate macros proportionally.
5. All macro values should be in grams (g), salt in milligrams (mg).

OUTPUT FORMAT (JSON ONLY):
{
  "meal_name": "Label based on time (Breakfast/Lunch/Dinner/Snack)",
  "items": [
    {
      "food": "Name of food",
      "qty": "Quantity",
      "calories": 0,
      "protein": 0,
      "carbs": 0,
      "fat": 0,
      "sugar": 0,
      "salt": 0
    }
  ],
  "total_calories": 0,
  "total_protein": 0,
  "total_carbs": 0,
  "total_fat": 0,
  "total_sugar": 0,
  "total_salt": 0
}
"""

@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user}')
    with model_lock:
        print(f'🤖 Using model: {current_model_name}')
    print(f'📋 Commands: cal!log, cal!today, cal!delete, cal!week, cal!history, cal!help')
    if DEV_USER_IDS and DEV_USER_IDS[0]:
        print(f'👨‍💻 Dev commands enabled for: {DEV_USER_IDS}')


# --- GLOBAL ERROR HANDLER ---
@bot.event
async def on_command_error(ctx, error):
    """Global error handler for all commands"""
    if isinstance(error, commands.MissingRequiredArgument):
        if ctx.command.name == 'log':
            embed = discord.Embed(
                title="❌ Missing Food Description",
                description="Please provide what you ate after the command.",
                color=discord.Color.red()
            )
            embed.add_field(
                name="Usage", 
                value="`cal!log <food description>`", 
                inline=False
            )
            embed.add_field(
                name="Examples", 
                value="• `cal!log 2 eggs and toast`\n• `cal!log fried chicken with rice`\n• `cal!log 100cal coffee`", 
                inline=False
            )
            await ctx.send(embed=embed)
        elif ctx.command.name == 'delete':
            await ctx.send("❌ Please provide a meal ID to delete. Usage: `cal!delete <meal_id>`\nFind meal IDs with `cal!today`")
        else:
            await ctx.send(f"❌ Missing required argument. Use `cal!commands` for help.")
    elif isinstance(error, commands.CommandNotFound):
        # Silently ignore unknown commands
        pass
    elif isinstance(error, commands.BadArgument):
        if ctx.command.name == 'delete':
            await ctx.send("❌ Invalid meal ID. Please provide a valid number.\nUse `cal!today` to see meal IDs.")
        else:
            await ctx.send(f"❌ Invalid argument provided. Use `cal!commands` for help.")
    else:
        # For other errors, log them but don't expose details to users
        print(f"Unhandled error in {ctx.command}: {error}")
        await ctx.send("❌ An unexpected error occurred. Please try again later.")


# --- COMMAND: LOG FOOD ---
@bot.command(name='log', aliases=['l'])
async def log_food(ctx, *, user_input: str):
    """Log a meal. Usage: cal!log <food description>"""
    # Validate input is not empty or just whitespace
    if not user_input or user_input.strip() == "":
        embed = discord.Embed(
            title="❌ Empty Food Description",
            description="Please describe what you ate.",
            color=discord.Color.red()
        )
        embed.add_field(name="Example", value="`cal!log 2 eggs and toast with butter`", inline=False)
        await ctx.send(embed=embed)
        return
    
    await ctx.message.add_reaction("⏳")

    try:
        # A. CALL GEMINI
        try:
            with model_lock:
                response = model.generate_content(f"{SYSTEM_PROMPT}\n\nUSER INPUT: {user_input}")
            
            if not response or not response.text:
                raise ValueError("Gemini returned an empty response")
                
        except Exception as gemini_error:
            await ctx.message.remove_reaction("⏳", bot.user)
            await ctx.message.add_reaction("❌")
            await ctx.send(
                f"❌ **AI Service Error**\n"
                f"Could not process your food description. This might be due to:\n"
                f"• API rate limits\n"
                f"• Network issues\n"
                f"• Service temporary unavailability\n\n"
                f"Please try again in a moment."
            )
            print(f"Gemini API Error: {gemini_error}")
            return
        
        # B. PARSE JSON
        try:
            # Clean up response (sometimes models add markdown backticks)
            cleaned_json = response.text.replace('```json', '').replace('```', '').strip()
            data = json.loads(cleaned_json)
            
            # Validate required fields
            if not isinstance(data, dict):
                raise ValueError("Response is not a valid JSON object")
            
            if 'items' not in data or not isinstance(data['items'], list):
                raise ValueError("Missing or invalid 'items' field")
            
            if len(data['items']) == 0:
                await ctx.message.remove_reaction("⏳", bot.user)
                await ctx.message.add_reaction("⚠️")
                await ctx.send(
                    f"⚠️ **No Food Items Detected**\n"
                    f"I couldn't identify any food items from: *\"{user_input}\"*\n\n"
                    f"Please try describing your food more clearly."
                )
                return
                
        except json.JSONDecodeError as json_error:
            await ctx.message.remove_reaction("⏳", bot.user)
            await ctx.message.add_reaction("❌")
            await ctx.send(
                f"❌ **Parsing Error**\n"
                f"The AI returned an invalid response format. Please try again.\n\n"
                f"If this persists, try rephrasing your food description."
            )
            print(f"JSON Parse Error: {json_error}")
            print(f"Raw response: {response.text}")
            return
        except ValueError as val_error:
            await ctx.message.remove_reaction("⏳", bot.user)
            await ctx.message.add_reaction("❌")
            await ctx.send(
                f"❌ **Invalid Response Structure**\n"
                f"{str(val_error)}\n\n"
                f"Please try again with a clearer food description."
            )
            print(f"Validation Error: {val_error}")
            return

        # C. SAVE TO SUPABASE
        try:
            # Generate short ID
            short_id = generate_short_id(str(ctx.author), datetime.now())
            
            payload = {
                "user_name": str(ctx.author),
                "short_id": short_id,
                "meal_name": data.get('meal_name', 'Snack'),
                "items": data.get('items', []),
                "total_calories": data.get('total_calories', 0),
                "total_protein": data.get('total_protein', 0),
                "total_carbs": data.get('total_carbs', 0),
                "total_fat": data.get('total_fat', 0),
                "total_sugar": data.get('total_sugar', 0),
                "total_salt": data.get('total_salt', 0)
            }
            
            # Execute insert
            result = supabase.table('food_logs').insert(payload).execute()
            
            if not result.data:
                raise ValueError("Database insert returned no data")
                
        except Exception as db_error:
            await ctx.message.remove_reaction("⏳", bot.user)
            await ctx.message.add_reaction("❌")
            await ctx.send(
                f"❌ **Database Error**\n"
                f"Failed to save your meal to the database.\n\n"
                f"Please check your database connection and try again."
            )
            print(f"Database Error: {db_error}")
            return

        # D. REPLY TO USER
        embed = discord.Embed(
            title=f"🍽️ {payload['meal_name']} Logged",
            color=discord.Color.green()
        )
        
        item_str = ""
        for item in payload['items']:
            item_str += f"• **{item['food']}** ({item['qty']}) - {item.get('calories', 0)} kcal\n"
            item_str += f"  P: {item.get('protein', 0)}g | C: {item.get('carbs', 0)}g | F: {item.get('fat', 0)}g\n"
        
        embed.add_field(name="Items", value=item_str or "No items", inline=False)
        
        # Macro summary
        macro_summary = (
            f"🔥 **Calories:** {payload['total_calories']} kcal\n"
            f"🥩 **Protein:** {payload['total_protein']}g\n"
            f"🍞 **Carbs:** {payload['total_carbs']}g\n"
            f"🧈 **Fat:** {payload['total_fat']}g"
        )
        embed.add_field(name="Nutrition Summary", value=macro_summary, inline=False)
        
        # Optional macros (sugar & salt)
        optional_macros = f"🍬 Sugar: {payload['total_sugar']}g | 🧂 Salt: {payload['total_salt']}mg"
        embed.add_field(name="Additional Info", value=optional_macros, inline=False)
        
        embed.set_footer(text="Saved to Supabase database")

        await ctx.send(embed=embed)
        await ctx.message.remove_reaction("⏳", bot.user)
        await ctx.message.add_reaction("✅")

    except Exception as e:
        # Catch-all for any unexpected errors
        await ctx.message.remove_reaction("⏳", bot.user)
        await ctx.message.add_reaction("❌")
        await ctx.send(
            f"❌ **Unexpected Error**\n"
            f"Something went wrong while processing your request.\n\n"
            f"Error details: `{str(e)}`"
        )
        print(f"Unexpected Error in log_food: {e}")
        import traceback
        traceback.print_exc()


# --- COMMAND: VIEW TODAY'S MEALS ---
@bot.command(name='today', aliases=['t'])
async def view_today(ctx):
    """View all meals logged today. Usage: cal!today"""
    try:
        user_name = str(ctx.author)
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        today_end = datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
        
        # Query Supabase for today's logs
        result = supabase.table('food_logs')\
            .select('*')\
            .eq('user_name', user_name)\
            .gte('created_at', today_start)\
            .lte('created_at', today_end)\
            .order('created_at', desc=False)\
            .execute()
        
        logs = result.data
        
        if not logs:
            await ctx.send("📭 No meals logged today. Start with `cal!log <food>`")
            return
        
        embed = discord.Embed(
            title="📅 Today's Meals",
            color=discord.Color.blue()
        )
        
        total_day_calories = 0
        total_day_protein = 0
        total_day_carbs = 0
        total_day_fat = 0
        total_day_sugar = 0
        total_day_salt = 0
        
        for log in logs:
            # Use short_id if available, fallback to id
            display_id = log.get('short_id', log['id'])
            meal_info = f"**ID:** `{display_id}`\n"
            for item in log.get('items', []):
                meal_info += f"• {item['food']} ({item['qty']}) - {item.get('calories', 0)} kcal\n"
            meal_info += f"**Subtotal:** {log['total_calories']} kcal"
            meal_info += f" | P: {log.get('total_protein', 0)}g | C: {log.get('total_carbs', 0)}g | F: {log.get('total_fat', 0)}g"
            
            embed.add_field(
                name=f"🍽️ {log['meal_name']}", 
                value=meal_info, 
                inline=False
            )
            total_day_calories += log.get('total_calories', 0)
            total_day_protein += log.get('total_protein', 0)
            total_day_carbs += log.get('total_carbs', 0)
            total_day_fat += log.get('total_fat', 0)
            total_day_sugar += log.get('total_sugar', 0)
            total_day_salt += log.get('total_salt', 0)
        
        # Daily totals with macros
        daily_summary = (
            f"🔥 **Calories:** {total_day_calories} kcal\n"
            f"🥩 **Protein:** {total_day_protein}g | 🍞 **Carbs:** {total_day_carbs}g | 🧈 **Fat:** {total_day_fat}g\n"
            f"🍬 Sugar: {total_day_sugar}g | 🧂 Salt: {total_day_salt}mg"
        )
        embed.add_field(name="━━━ Daily Totals ━━━", value=daily_summary, inline=False)
        embed.set_footer(text=f"Use cal!delete <ID> to remove a meal")
        
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
        print(f"Error: {e}")


# --- COMMAND: DELETE A MEAL ---
@bot.command(name='delete', aliases=['d'])
async def delete_meal(ctx, meal_id: str):
    """Delete a specific meal by ID. Usage: cal!delete <meal_id>"""
    try:
        user_name = str(ctx.author)
        
        # Try to find meal by short_id first, then by numeric id
        check = supabase.table('food_logs')\
            .select('*')\
            .eq('user_name', user_name)\
            .eq('short_id', meal_id.upper())\
            .execute()
        
        # If not found by short_id and meal_id is numeric, try by id
        if not check.data and meal_id.isdigit():
            check = supabase.table('food_logs')\
                .select('*')\
                .eq('user_name', user_name)\
                .eq('id', int(meal_id))\
                .execute()
        
        if not check.data:
            await ctx.send(f"❌ Meal ID `{meal_id}` not found or doesn't belong to you.")
            return
        
        meal = check.data[0]
        display_id = meal.get('short_id', meal['id'])
        
        # Delete the meal by its database id
        supabase.table('food_logs').delete().eq('id', meal['id']).execute()
        
        embed = discord.Embed(
            title="🗑️ Meal Deleted",
            description=f"Successfully removed **{meal['meal_name']}** ({meal['total_calories']} kcal)",
            color=discord.Color.red()
        )
        embed.set_footer(text=f"ID: {display_id}")
        
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
        print(f"Error: {e}")


# --- COMMAND: CURRENT WEEK SUMMARY ---
@bot.command(name='week', aliases=['w'])
async def view_week(ctx):
    """View calorie summary for the current week. Usage: cal!week"""
    try:
        user_name = str(ctx.author)
        
        # Calculate start of current week (Monday)
        today = datetime.now()
        start_of_week = today - timedelta(days=today.weekday())
        start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        
        result = supabase.table('food_logs')\
            .select('*')\
            .eq('user_name', user_name)\
            .gte('created_at', start_of_week.isoformat())\
            .order('created_at', desc=False)\
            .execute()
        
        logs = result.data
        
        if not logs:
            await ctx.send("📭 No meals logged this week yet.")
            return
        
        # Group by day
        daily_totals = {}
        for log in logs:
            log_date = datetime.fromisoformat(log['created_at'].replace('Z', '+00:00')).strftime('%A, %b %d')
            if log_date not in daily_totals:
                daily_totals[log_date] = {'calories': 0, 'protein': 0, 'carbs': 0, 'fat': 0}
            daily_totals[log_date]['calories'] += log.get('total_calories', 0)
            daily_totals[log_date]['protein'] += log.get('total_protein', 0)
            daily_totals[log_date]['carbs'] += log.get('total_carbs', 0)
            daily_totals[log_date]['fat'] += log.get('total_fat', 0)
        
        embed = discord.Embed(
            title="📊 This Week's Summary",
            description=f"Week of {start_of_week.strftime('%b %d, %Y')}",
            color=discord.Color.purple()
        )
        
        total_week = 0
        total_week_protein = 0
        total_week_carbs = 0
        total_week_fat = 0
        
        for day, data in daily_totals.items():
            day_info = f"{data['calories']} kcal\nP: {data['protein']}g | C: {data['carbs']}g | F: {data['fat']}g"
            embed.add_field(name=day, value=day_info, inline=True)
            total_week += data['calories']
            total_week_protein += data['protein']
            total_week_carbs += data['carbs']
            total_week_fat += data['fat']
        
        avg_daily = total_week // len(daily_totals) if daily_totals else 0
        avg_protein = total_week_protein // len(daily_totals) if daily_totals else 0
        avg_carbs = total_week_carbs // len(daily_totals) if daily_totals else 0
        avg_fat = total_week_fat // len(daily_totals) if daily_totals else 0
        
        embed.add_field(name="━━━━━━━━━━━━━━━", value="\u200b", inline=False)
        embed.add_field(name="🔥 Week Total", value=f"**{total_week} kcal**\nP: {total_week_protein}g | C: {total_week_carbs}g | F: {total_week_fat}g", inline=True)
        embed.add_field(name="📈 Daily Avg", value=f"**{avg_daily} kcal**\nP: {avg_protein}g | C: {avg_carbs}g | F: {avg_fat}g", inline=True)
        
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
        print(f"Error: {e}")


# --- COMMAND: ALL WEEKS HISTORY ---
@bot.command(name='history', aliases=['h'])
async def view_history(ctx):
    """View calorie summary for all past weeks. Usage: cal!history"""
    try:
        user_name = str(ctx.author)
        
        result = supabase.table('food_logs')\
            .select('*')\
            .eq('user_name', user_name)\
            .order('created_at', desc=False)\
            .execute()
        
        logs = result.data
        
        if not logs:
            await ctx.send("📭 No meal history found. Start logging with `cal!log <food>`")
            return
        
        # Group by week
        weekly_totals = {}
        for log in logs:
            log_date = datetime.fromisoformat(log['created_at'].replace('Z', '+00:00'))
            # Get start of that week (Monday)
            week_start = log_date - timedelta(days=log_date.weekday())
            week_key = week_start.strftime('%Y-%m-%d')
            week_label = f"Week of {week_start.strftime('%b %d, %Y')}"
            
            if week_key not in weekly_totals:
                weekly_totals[week_key] = {'label': week_label, 'calories': 0, 'meals': 0}
            weekly_totals[week_key]['calories'] += log.get('total_calories', 0)
            weekly_totals[week_key]['meals'] += 1
        
        embed = discord.Embed(
            title="📚 Calorie History (All Weeks)",
            color=discord.Color.gold()
        )
        
        grand_total = 0
        # Sort weeks chronologically (most recent first)
        sorted_weeks = sorted(weekly_totals.items(), reverse=True)
        
        for week_key, data in sorted_weeks[:10]:  # Limit to 10 most recent weeks
            embed.add_field(
                name=data['label'], 
                value=f"**{data['calories']} kcal** ({data['meals']} meals)", 
                inline=False
            )
            grand_total += data['calories']
        
        if len(sorted_weeks) > 10:
            embed.set_footer(text=f"Showing 10 most recent weeks • Total all-time: {grand_total} kcal")
        else:
            embed.set_footer(text=f"Total all-time: {grand_total} kcal")
        
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
        print(f"Error: {e}")


# --- DEV COMMAND: LIST MODELS ---
@bot.command(name='models', aliases=['lm'])
async def list_models(ctx):
    """[DEV ONLY] List all available Gemini models. Usage: cal!models"""
    if not is_dev(ctx.author.id):
        await ctx.send("❌ This command is only available to developers.")
        return
    
    try:
        await ctx.message.add_reaction("⏳")
        
        # Fetch all available models
        models = genai.list_models()
        
        with model_lock:
            current = current_model_name
        
        embed = discord.Embed(
            title="🤖 Available Gemini Models",
            description=f"Current model: **{current}**",
            color=discord.Color.blue()
        )
        
        # Filter for generative models and group by type
        gemini_models = []
        for m in models:
            # Only include models that support generateContent
            if 'generateContent' in m.supported_generation_methods:
                model_info = {
                    'name': m.name.replace('models/', ''),
                    'display_name': m.display_name,
                    'description': m.description[:100] + '...' if len(m.description) > 100 else m.description
                }
                gemini_models.append(model_info)
        
        # Sort models by name
        gemini_models.sort(key=lambda x: x['name'])
        
        # Display models in groups
        model_list = ""
        for idx, m in enumerate(gemini_models, 1):
            current_marker = "✅ " if m['name'] == current else ""
            model_list += f"{current_marker}**{idx}. {m['name']}**\n"
            if m.get('display_name'):
                model_list += f"   {m['display_name']}\n"
            model_list += "\n"
            
            # Discord embed field limit is 1024 chars
            if len(model_list) > 950:
                embed.add_field(name="Models (continued)", value=model_list, inline=False)
                model_list = ""
        
        if model_list:
            embed.add_field(name="Available Models", value=model_list, inline=False)
        
        embed.set_footer(text=f"Use cal!setmodel <model_name> to switch models")
        
        await ctx.send(embed=embed)
        await ctx.message.remove_reaction("⏳", bot.user)
        await ctx.message.add_reaction("✅")
        
    except Exception as e:
        await ctx.message.remove_reaction("⏳", bot.user)
        await ctx.message.add_reaction("❌")
        await ctx.send(f"❌ Error fetching models: {str(e)}")
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


# --- DEV COMMAND: SET MODEL ---
@bot.command(name='setmodel', aliases=['sm'])
async def set_model(ctx, *, model_name: str):
    """[DEV ONLY] Change the Gemini model. Usage: cal!setmodel <model_name>"""
    global model, current_model_name
    
    if not is_dev(ctx.author.id):
        await ctx.send("❌ This command is only available to developers.")
        return
    
    try:
        await ctx.message.add_reaction("⏳")
        
        # Clean up model name (remove 'models/' prefix if present)
        model_name = model_name.replace('models/', '').strip()
        
        # Try to create a new model instance
        try:
            new_model = genai.GenerativeModel(model_name)
            # Test the model with a simple request
            test_response = new_model.generate_content("Say 'OK'")
            if not test_response:
                raise ValueError("Model test failed")
        except Exception as model_error:
            await ctx.message.remove_reaction("⏳", bot.user)
            await ctx.message.add_reaction("❌")
            await ctx.send(
                f"❌ **Invalid Model**\n"
                f"Could not switch to model: `{model_name}`\n\n"
                f"Error: {str(model_error)}\n\n"
                f"Use `cal!models` to see available models."
            )
            return
        
        # Update the global model (protected by lock)
        with model_lock:
            old_model = current_model_name
            model = new_model
            current_model_name = model_name
        
        embed = discord.Embed(
            title="✅ Model Changed",
            description=f"Successfully switched to **{model_name}**",
            color=discord.Color.green()
        )
        embed.add_field(name="Previous Model", value=old_model, inline=True)
        embed.add_field(name="New Model", value=model_name, inline=True)
        embed.set_footer(text="All future requests will use this model")
        
        await ctx.send(embed=embed)
        await ctx.message.remove_reaction("⏳", bot.user)
        await ctx.message.add_reaction("✅")
        
    except Exception as e:
        await ctx.message.remove_reaction("⏳", bot.user)
        await ctx.message.add_reaction("❌")
        await ctx.send(f"❌ Error changing model: {str(e)}")
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


# --- COMMAND: HELP ---
@bot.command(name='commands', aliases=['c'])
async def show_help(ctx):
    """Show all available commands. Usage: cal!commands"""
    embed = discord.Embed(
        title="🤖 Calorie Tracker Commands",
        description="Track your meals with AI-powered calorie estimation",
        color=discord.Color.teal()
    )
    
    embed.add_field(
        name="cal!log <food>", 
        value="Log a meal (e.g., `cal!log 2 eggs and toast with butter`)", 
        inline=False
    )
    embed.add_field(
        name="cal!today", 
        value="View all meals logged today", 
        inline=False
    )
    embed.add_field(
        name="cal!delete <ID>", 
        value="Delete a meal by its ID (shown in cal!today)", 
        inline=False
    )
    embed.add_field(
        name="cal!week", 
        value="View calorie summary for the current week", 
        inline=False
    )
    embed.add_field(
        name="cal!history", 
        value="View calorie summary for all past weeks", 
        inline=False
    )
    
    # Add dev commands if user is a developer
    if is_dev(ctx.author.id):
        embed.add_field(
            name="━━━━━━ DEV ONLY ━━━━━━",
            value="\u200b",
            inline=False
        )
        embed.add_field(
            name="cal!models",
            value="List all available Gemini models",
            inline=False
        )
        embed.add_field(
            name="cal!setmodel <name>",
            value="Change the active Gemini model",
            inline=False
        )
    
    embed.set_footer(text="💡 Tip: Mention 'oily' or 'fried' for auto oil adjustment!")
    
    await ctx.send(embed=embed)


# Run the bot
bot.run(DISCORD_TOKEN)