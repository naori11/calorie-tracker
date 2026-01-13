import os
import json
import discord
import google.generativeai as genai
from discord.ext import commands
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime, timedelta

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

# 4. Setup Discord with cal! prefix
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="cal!", intents=intents)

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
    print(f'📋 Commands: cal!log, cal!today, cal!delete, cal!week, cal!history, cal!help')


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
@bot.command(name='log')
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
            payload = {
                "user_name": str(ctx.author),
                "meal_name": data.get('meal_name', 'Snack'),
                "items": data.get('items', []),
                "total_calories": data.get('total_calories', 0)
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
            item_str += f"• **{item['food']}** ({item['qty']}) - {item['calories']} kcal\n"
        
        embed.add_field(name="Items", value=item_str or "No items", inline=False)
        embed.add_field(name="Total Calories", value=f"**{payload['total_calories']} kcal**", inline=False)
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
@bot.command(name='today')
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
        for log in logs:
            meal_info = f"**ID:** `{log['id']}`\n"
            for item in log.get('items', []):
                meal_info += f"• {item['food']} ({item['qty']}) - {item['calories']} kcal\n"
            meal_info += f"**Subtotal:** {log['total_calories']} kcal"
            
            embed.add_field(
                name=f"🍽️ {log['meal_name']}", 
                value=meal_info, 
                inline=False
            )
            total_day_calories += log.get('total_calories', 0)
        
        embed.add_field(name="━━━━━━━━━━━━━━━", value=f"🔥 **Total Today: {total_day_calories} kcal**", inline=False)
        embed.set_footer(text=f"Use cal!delete <ID> to remove a meal")
        
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
        print(f"Error: {e}")


# --- COMMAND: DELETE A MEAL ---
@bot.command(name='delete')
async def delete_meal(ctx, meal_id: int):
    """Delete a specific meal by ID. Usage: cal!delete <meal_id>"""
    try:
        user_name = str(ctx.author)
        
        # First verify the meal belongs to the user
        check = supabase.table('food_logs')\
            .select('*')\
            .eq('id', meal_id)\
            .eq('user_name', user_name)\
            .execute()
        
        if not check.data:
            await ctx.send(f"❌ Meal ID `{meal_id}` not found or doesn't belong to you.")
            return
        
        meal = check.data[0]
        
        # Delete the meal
        supabase.table('food_logs').delete().eq('id', meal_id).execute()
        
        embed = discord.Embed(
            title="🗑️ Meal Deleted",
            description=f"Successfully removed **{meal['meal_name']}** ({meal['total_calories']} kcal)",
            color=discord.Color.red()
        )
        embed.set_footer(text=f"ID: {meal_id}")
        
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
        print(f"Error: {e}")


# --- COMMAND: CURRENT WEEK SUMMARY ---
@bot.command(name='week')
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
                daily_totals[log_date] = 0
            daily_totals[log_date] += log.get('total_calories', 0)
        
        embed = discord.Embed(
            title="📊 This Week's Summary",
            description=f"Week of {start_of_week.strftime('%b %d, %Y')}",
            color=discord.Color.purple()
        )
        
        total_week = 0
        for day, cals in daily_totals.items():
            embed.add_field(name=day, value=f"{cals} kcal", inline=True)
            total_week += cals
        
        avg_daily = total_week // len(daily_totals) if daily_totals else 0
        
        embed.add_field(name="━━━━━━━━━━━━━━━", value="\u200b", inline=False)
        embed.add_field(name="🔥 Week Total", value=f"**{total_week} kcal**", inline=True)
        embed.add_field(name="📈 Daily Avg", value=f"**{avg_daily} kcal**", inline=True)
        
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
        print(f"Error: {e}")


# --- COMMAND: ALL WEEKS HISTORY ---
@bot.command(name='history')
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


# --- COMMAND: HELP ---
@bot.command(name='commands')
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
    
    embed.set_footer(text="💡 Tip: Mention 'oily' or 'fried' for auto oil adjustment!")
    
    await ctx.send(embed=embed)


# Run the bot
bot.run(DISCORD_TOKEN)