import discord
from discord import app_commands
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
import os
from datetime import datetime
from pymongo import MongoClient

# ============================
TOKEN = os.getenv("DISCORD_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
# ============================

# --- MongoDB ---
mongo = MongoClient(MONGO_URI)
db = mongo["speakbot"]
configs = db["configs"]


def get_log_channel(guild_id: str):
    doc = configs.find_one({"guild_id": guild_id})
    return doc["log_channel"] if doc else None


def set_log_channel(guild_id: str, channel_id: str):
    configs.update_one(
        {"guild_id": guild_id},
        {"$set": {"log_channel": channel_id}},
        upsert=True
    )


def remove_log_channel(guild_id: str):
    configs.update_one(
        {"guild_id": guild_id},
        {"$unset": {"log_channel": ""}}
    )


# --- Bot setup ---
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


# --- Helper : envoyer un log ---
async def send_log(guild: discord.Guild, embed: discord.Embed):
    log_channel_id = get_log_channel(str(guild.id))
    if not log_channel_id:
        return
    channel = guild.get_channel(int(log_channel_id))
    if channel:
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass


# --- Liste déroulante pour /config ---
class ConfigOption(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Logs",
                value="logs",
                description="Définir ou supprimer le salon de logs",
                emoji="📋"
            ),
            # Ajoute d'autres options ici plus tard
        ]
        super().__init__(
            placeholder="Choisir une option...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "logs":
            await interaction.response.send_modal(LogsModal())


class ConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(ConfigOption())


# --- Modal pour configurer les logs ---
class LogsModal(discord.ui.Modal, title="Configuration des logs"):
    channel_input = discord.ui.TextInput(
        label="Salon de logs",
        placeholder="Mentionne un salon (#salon) ou entre son ID — laisse vide pour supprimer",
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        value = self.channel_input.value.strip()

        # Supprimer les logs si vide
        if not value:
            remove_log_channel(guild_id)
            await interaction.response.send_message("✅ Logs désactivés.", ephemeral=True)
            return

        channel_id = value.replace("<#", "").replace(">", "")
        try:
            channel = interaction.guild.get_channel(int(channel_id))
            if channel is None:
                await interaction.response.send_message("❌ Salon introuvable.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message(
                "❌ Valeur invalide. Mentionne un salon avec # ou entre son ID.",
                ephemeral=True
            )
            return

        set_log_channel(guild_id, str(channel.id))
        await interaction.response.send_message(
            f"✅ Salon de logs défini sur {channel.mention}", ephemeral=True
        )


# --- /config ---
@tree.command(name="config", description="Configure le bot (admins uniquement)")
@app_commands.checks.has_permissions(administrator=True)
async def config_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "⚙️ **Configuration** — Choisis une option :",
        view=ConfigView(),
        ephemeral=True
    )


@config_cmd.error
async def config_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.",
            ephemeral=True
        )


# --- /speak ---
@tree.command(name="speak", description="Fait parler le bot à ta place")
@app_commands.describe(
    message="Le message que le bot va dire",
    answer="ID ou lien du message auquel répondre (optionnel)"
)
async def speak(interaction: discord.Interaction, message: str, answer: str = None):
    await interaction.response.send_message("✅", ephemeral=True)

    reference = None
    if answer:
        message_id = answer.strip().split("/")[-1]
        try:
            target = await interaction.channel.fetch_message(int(message_id))
            reference = target.to_reference()
        except Exception:
            await interaction.followup.send(
                "❌ Message introuvable. Vérifie l'ID ou le lien.",
                ephemeral=True
            )
            return

    await interaction.channel.send(message, reference=reference)

    embed = discord.Embed(
        title="📢 /speak utilisé",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Utilisateur", value=f"{interaction.user} (`{interaction.user.id}`)", inline=False)
    embed.add_field(name="Salon", value=interaction.channel.mention, inline=False)
    embed.add_field(name="Message envoyé", value=message[:1024], inline=False)
    if answer:
        embed.add_field(name="En réponse à", value=answer, inline=False)
    embed.set_footer(text=f"Serveur : {interaction.guild.name}")
    await send_log(interaction.guild, embed)


# --- /aide ---
@tree.command(name="aide", description="Affiche l'aide du bot")
async def aide(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 Aide du bot",
        description="Voici les commandes disponibles :",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="/speak `message`",
        value="Fait dire un message au bot dans le salon, comme s'il parlait tout seul.\n**Exemple :** `/speak Yo tout le monde !`",
        inline=False
    )
    embed.add_field(
        name="/speak `message` `answer`",
        value="Fait répondre le bot à un message spécifique. Donne l'**ID** ou le **lien** du message.\n**Exemple :** `/speak Bien sûr ! answer:123456789012345678`",
        inline=False
    )
    embed.add_field(name="/aide", value="Affiche ce message d'aide.", inline=False)
    embed.add_field(name="/config", value="Configure le bot (salon de logs, etc.). Admin uniquement.", inline=False)
    embed.set_footer(text="Le bot supprime automatiquement la trace de tes commandes.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

    log_embed = discord.Embed(
        title="📖 /aide utilisé",
        color=discord.Color.blurple(),
        timestamp=datetime.utcnow()
    )
    log_embed.add_field(name="Utilisateur", value=f"{interaction.user} (`{interaction.user.id}`)", inline=False)
    log_embed.add_field(name="Salon", value=interaction.channel.mention, inline=False)
    log_embed.set_footer(text=f"Serveur : {interaction.guild.name}")
    await send_log(interaction.guild, log_embed)


# --- Events ---
@client.event
async def on_ready():
    await tree.sync()
    print(f"Bot connecté en tant que {client.user}")


# --- Health server pour Railway ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass


def run_health_server():
    server = HTTPServer(("0.0.0.0", 8080), HealthHandler)
    server.serve_forever()


Thread(target=run_health_server, daemon=True).start()
client.run(TOKEN)
