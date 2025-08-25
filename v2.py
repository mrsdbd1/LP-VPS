import random
import logging
import subprocess
import sys
import os
import re
import time
import concurrent.futures
import discord
from discord.ext import commands, tasks
import docker
import asyncio
from discord import app_commands
from discord.ui import Button, View, Select
import string
from datetime import datetime, timedelta
from typing import Optional, Literal

TOKEN = ''
RAM_LIMIT = '64g'
SERVER_LIMIT = 1000
database_file = 'database.txt'
PUBLIC_IP = '138.68.79.95'

# Admin user IDs - add your admin user IDs here
ADMIN_IDS = [1384852296881537064]  # Replace with actual admin IDs

intents = discord.Intents.default()
intents.messages = False
intents.message_content = False

bot = commands.Bot(command_prefix='/', intents=intents)
client = docker.from_env()

# Helper functions
def is_admin(user_id):
    return user_id in ADMIN_IDS

def generate_random_string(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def generate_random_port(): 
    return random.randint(1025, 65535)

def parse_time_to_seconds(time_str):
    """Convert time string like '1d', '2h', '30m', '45s', '1y', '3M' to seconds"""
    if not time_str:
        return None
    
    units = {
        's': 1,               # seconds
        'm': 60,              # minutes
        'h': 3600,            # hours
        'd': 86400,           # days
        'M': 2592000,         # months (30 days)
        'y': 31536000         # years (365 days)
    }
    
    unit = time_str[-1]
    if unit in units and time_str[:-1].isdigit():
        return int(time_str[:-1]) * units[unit]
    elif time_str.isdigit():
        return int(time_str) * 86400  # Default to days if no unit specified
    return None

def format_expiry_date(seconds_from_now):
    """Convert seconds from now to a formatted date string"""
    if not seconds_from_now:
        return None
    
    expiry_date = datetime.now() + timedelta(seconds=seconds_from_now)
    return expiry_date.strftime("%Y-%m-%d %H:%M:%S")

def add_to_database(user, container_name, ssh_command, ram_limit=None, cpu_limit=None, creator=None, expiry=None, os_type="Ubuntu 22.04"):
    with open(database_file, 'a') as f:
        f.write(f"{user}|{container_name}|{ssh_command}|{ram_limit or '2048'}|{cpu_limit or '1'}|{creator or user}|{os_type}|{expiry or 'None'}\n")

def remove_from_database(container_id):
    if not os.path.exists(database_file):
        return
    with open(database_file, 'r') as f:
        lines = f.readlines()
    with open(database_file, 'w') as f:
        for line in lines:
            if container_id not in line:
                f.write(line)

def get_all_containers():
    if not os.path.exists(database_file):
        return []
    with open(database_file, 'r') as f:
        return [line.strip() for line in f.readlines()]

def get_container_stats(container_id):
    try:
        # Get memory usage
        mem_stats = subprocess.check_output(["docker", "stats", container_id, "--no-stream", "--format", "{{.MemUsage}}"]).decode().strip()
        
        # Get CPU usage
        cpu_stats = subprocess.check_output(["docker", "stats", container_id, "--no-stream", "--format", "{{.CPUPerc}}"]).decode().strip()
        
        # Get container status
        status = subprocess.check_output(["docker", "inspect", "--format", "{{.State.Status}}", container_id]).decode().strip()
        
        return {
            "memory": mem_stats,
            "cpu": cpu_stats,
            "status": "🟢 Running" if status == "running" else "🔴 Stopped"
        }
    except Exception:
        return {"memory": "N/A", "cpu": "N/A", "status": "🔴 Stopped"}

def get_system_stats():
    try:
        # Get total memory usage
        total_mem = subprocess.check_output(["free", "-m"]).decode().strip()
        mem_lines = total_mem.split('\n')
        if len(mem_lines) >= 2:
            mem_values = mem_lines[1].split()
            total_mem = mem_values[1]
            used_mem = mem_values[2]
            
        # Get disk usage
        disk_usage = subprocess.check_output(["df", "-h", "/"]).decode().strip()
        disk_lines = disk_usage.split('\n')
        if len(disk_lines) >= 2:
            disk_values = disk_lines[1].split()
            total_disk = disk_values[1]
            used_disk = disk_values[2]
            
        return {
            "total_memory": f"{total_mem}GB",
            "used_memory": f"{used_mem}GB",
            "total_disk": total_disk,
            "used_disk": used_disk
        }
    except Exception as e:
        return {
            "total_memory": "N/A",
            "used_memory": "N/A",
            "total_disk": "N/A",
            "used_disk": "N/A",
            "error": str(e)
        }

async def capture_ssh_session_line(process):
    while True:
        output = await process.stdout.readline()
        if not output:
            break
        output = output.decode('utf-8').strip()
        if "ssh session:" in output:
            return output.split("ssh session:")[1].strip()
    return None

def get_ssh_command_from_database(container_id):
    if not os.path.exists(database_file):
        return None
    with open(database_file, 'r') as f:
        for line in f:
            if container_id in line:
                parts = line.strip().split('|')
                if len(parts) >= 3:
                    return parts[2]
    return None

def get_user_servers(user):
    if not os.path.exists(database_file):
        return []
    servers = []
    with open(database_file, 'r') as f:
        for line in f:
            if line.startswith(user):
                servers.append(line.strip())
    return servers

def count_user_servers(user):
    return len(get_user_servers(user))

def get_container_id_from_database(user, container_name=None):
    servers = get_user_servers(user)
    if servers:
        if container_name:
            for server in servers:
                parts = server.split('|')
                if len(parts) >= 2 and container_name in parts[1]:
                    return parts[1]
            return None
        else:
            return servers[0].split('|')[1]
    return None

# OS Selection dropdown for deploy command
# OS Selection dropdown for deploy command
class OSSelectView(View):
    def __init__(self, callback):
        super().__init__(timeout=60)
        self.callback = callback
        
        # Create the OS selection dropdown
        select = Select(
            placeholder="Select an operating system",
            options=[
                discord.SelectOption(label="Ubuntu 22.04", description="Latest LTS Ubuntu release", emoji="🐧", value="ubuntu"),
                discord.SelectOption(label="Debian 12", description="Stable Debian release", emoji="🐧", value="debian")
            ]
        )
        
        select.callback = self.select_callback
        self.add_item(select)
        
    async def select_callback(self, interaction: discord.Interaction):
        selected_os = interaction.data["values"][0]
        await interaction.response.defer()
        await self.callback(interaction, selected_os)

# Confirmation dialog class for delete operations
# Confirmation dialog class for delete operations
class ConfirmView(View):
    def __init__(self, container_id, container_name, is_delete_all=False):
        super().__init__(timeout=60)
        self.container_id = container_id
        self.container_name = container_name
        self.is_delete_all = is_delete_all
        
    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # First, acknowledge the interaction to prevent timeout
        await interaction.response.defer(ephemeral=False)
        
        try:
            if self.is_delete_all:
                # Delete all VPS instances
                containers = get_all_containers()
                deleted_count = 0
                
                for container_info in containers:
                    parts = container_info.split('|')
                    if len(parts) >= 2:
                        container_id = parts[1]
                        try:
                            subprocess.run(["docker", "stop", container_id], check=True, stderr=subprocess.DEVNULL)
                            subprocess.run(["docker", "rm", container_id], check=True, stderr=subprocess.DEVNULL)
                            deleted_count += 1
                        except Exception:
                            pass
                
                # Clear the database file
                with open(database_file, 'w') as f:
                    f.write('')
                    
                embed = discord.Embed(
                    title=" All VPS Deleted",
                    description=f"Successfully deleted {deleted_count} VPS instances.",
                    color=0x2400ff
                )
                # Use followup instead of edit_message
                await interaction.followup.send(embed=embed)
                
                # Disable all buttons
                for child in self.children:
                    child.disabled = True
                
            else:
                # Delete single VPS instance
                try:
                    subprocess.run(["docker", "stop", self.container_id], check=True, stderr=subprocess.DEVNULL)
                    subprocess.run(["docker", "rm", self.container_id], check=True, stderr=subprocess.DEVNULL)
                    remove_from_database(self.container_id)
                    
                    embed = discord.Embed(
                        title=" VPS Deleted",
                        description=f"Successfully deleted VPS instance `{self.container_name}`.",
                        color=0x2400ff
                    )
                    # Use followup instead of edit_message
                    await interaction.followup.send(embed=embed)
                    
                    # Disable all buttons
                    for child in self.children:
                        child.disabled = True
                    
                except Exception as e:
                    embed = discord.Embed(
                        title="❌ Error",
                        description=f"Failed to delete VPS instance: {str(e)}",
                        color=0x2400ff
                    )
                    await interaction.followup.send(embed=embed)
        except Exception as e:
            # Handle any unexpected errors
            try:
                await interaction.followup.send(f"An error occurred: {str(e)}")
            except:
                pass
    
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # First, acknowledge the interaction to prevent timeout
        await interaction.response.defer(ephemeral=False)
        
        embed = discord.Embed(
            title="🚫 Operation Cancelled",
            description="The delete operation has been cancelled.",
            color=0x2400ff
        )
        # Use followup instead of edit_message
        await interaction.followup.send(embed=embed)
        
        # Disable all buttons
        for child in self.children:
            child.disabled = True

@bot.event
async def on_ready():
    change_status.start()
    print(f'🚀 Bot is ready. Logged in as {bot.user}')
    await bot.tree.sync()

@tasks.loop(seconds=5)
async def change_status():
    try:
        if os.path.exists(database_file):
            with open(database_file, 'r') as f:
                lines = f.readlines()
                instance_count = len(lines)
        else:
            instance_count = 0

        status = f" LP NODES {instance_count} VPS"
        await bot.change_presence(activity=discord.Game(name=status))
    except Exception as e:
        print(f"Failed to update status: {e}")

@bot.tree.command(name="nodedmin", description="📊 Admin: Lists all VPSs, their details, and SSH commands")
async def nodedmin(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        embed = discord.Embed(
            title="❌ Access Denied",
            description="You don't have permission to use this command.",
            color=0x2400ff
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Use defer to handle potentially longer processing time
    await interaction.response.defer()

    if not os.path.exists(database_file):
        embed = discord.Embed(
            title="VPS Instances",
            description="No VPS data available.",
            color=0x2400ff
        )
        await interaction.followup.send(embed=embed)
        return

    embed = discord.Embed(
        title="All VPS Instances",
        description="Detailed information about all VPS instances",
        color=0x2400ff
    )
    
    with open(database_file, 'r') as f:
        lines = f.readlines()
    
    # If there are too many instances, we might need multiple embeds
    embeds = []
    current_embed = embed
    field_count = 0
    
    for line in lines:
        parts = line.strip().split('|')
        
        # Check if we need a new embed (Discord has a 25 field limit per embed)
        if field_count >= 25:
            embeds.append(current_embed)
            current_embed = discord.Embed(
                title="📊 All VPS Instances (Continued)",
                description="Detailed information about all VPS instances",
                color=0x2400ff
            )
            field_count = 0
        
        if len(parts) >= 8:
            user, container_name, ssh_command, ram, cpu, creator, os_type, expiry = parts
            stats = get_container_stats(container_name)
            
            current_embed.add_field(
                name=f"🖥️ {container_name} ({stats['status']})",
                value=f"🪩 **User:** {user}\n"
                      f"💾 **RAM:** {ram}GB\n"
                      f"🔥 **CPU:** {cpu} cores\n"
                      f"🌐 **OS:** {os_type}\n"
                      f"👑 **Creator:** {creator}\n"
                      f"🔑 **SSH:** `{ssh_command}`",
                inline=False
            )
            field_count += 1
        elif len(parts) >= 3:
            user, container_name, ssh_command = parts
            stats = get_container_stats(container_name)
            
            current_embed.add_field(
                name=f"🖥️ {container_name} ({stats['status']})",
                value=f"👤 **User:** {user}\n"
                      f"🔑 **SSH:** `{ssh_command}`",
                inline=False
            )
            field_count += 1
    
    # Add the last embed if it has fields
    if field_count > 0:
        embeds.append(current_embed)
    
    # Send all embeds
    if not embeds:
        await interaction.followup.send("No VPS instances found.")
        return
        
    for i, embed in enumerate(embeds):
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="node", description="☠️ Shows system resource usage and VPS status")
async def node_stats(interaction: discord.Interaction):
    await interaction.response.defer()
    
    system_stats = get_system_stats()
    containers = get_all_containers()
    
    embed = discord.Embed(
        title="📊 Panel Node Dashboard",
        description="📡 lp nodes",
        color=0x2400ff
    )
    
    embed.add_field(
        name="🔥 Memory Usage",
        value=f"Used: {system_stats['used_memory']} / Total: {system_stats['total_memory']}",
        inline=False
    )
    
    embed.add_field(
        name="💾 Storage Usage",
        value=f"Used: {system_stats['used_disk']} / Total: {system_stats['total_disk']}",
        inline=False
    )
    
    embed.add_field(
        name=f"💙 VPS ({len(containers)})",
        value="List of all VPS instances and their status:",
        inline=False
    )
    
    for container_info in containers:
        parts = container_info.split('|')
        if len(parts) >= 2:
            container_id = parts[1]
            stats = get_container_stats(container_id)
            embed.add_field(
                name=f"{container_id}",
                value=f"Status: {stats['status']}\nMemory: {stats['memory']}\nCPU: {stats['cpu']}",
                inline=True
            )
    
    await interaction.followup.send(embed=embed)

async def regen_ssh_command(interaction: discord.Interaction, container_name: str):
    user = str(interaction.user)
    container_id = get_container_id_from_database(user, container_name)

    if not container_id:
        embed = discord.Embed(
            title="❌ Not Found",
            description="No active instance found with that name for your user.",
            color=0x2400ff
        )
        await interaction.response.send_message(embed=embed)
        return

    try:
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        embed = discord.Embed(
            title="❌ Error",
            description=f"Error executing tmate in Docker container: {e}",
            color=0x2400ff
        )
        await interaction.response.send_message(embed=embed)
        return

    ssh_session_line = await capture_ssh_session_line(exec_cmd)
    if ssh_session_line:
        # Update SSH command in database
        if os.path.exists(database_file):
            with open(database_file, 'r') as f:
                lines = f.readlines()
            with open(database_file, 'w') as f:
                for line in lines:
                    if container_id in line:
                        parts = line.strip().split('|')
                        if len(parts) >= 3:
                            parts[2] = ssh_session_line
                            f.write('|'.join(parts) + '\n')
                    else:
                        f.write(line)
        
        # Send DM with new SSH command
        dm_embed = discord.Embed(
            title="🔄 New SSH Session Generated",
            description="Your SSH session has been regenerated successfully.",
            color=0x2400ff
        )
        dm_embed.add_field(
            name="🔑 SSH Connection Command",
            value=f"```{ssh_session_line}```",
            inline=False
        )
        await interaction.user.send(embed=dm_embed)
        
        # Send public success message
        success_embed = discord.Embed(
            title="✅ SSH Session Regenerated",
            description="New SSH session generated. Check your DMs for details.",
            color=0x2400ff
        )
        await interaction.response.send_message(embed=success_embed)
    else:
        error_embed = discord.Embed(
            title="❌ Failed",
            description="Failed to generate new SSH session.",
            color=0x2400ff
        )
        await interaction.response.send_message(embed=error_embed)

async def start_server(interaction: discord.Interaction, container_name: str):
    user = str(interaction.user)
    container_id = get_container_id_from_database(user, container_name)

    if not container_id:
        embed = discord.Embed(
            title="❌ Not Found",
            description="No instance found with that name for your user.",
            color=0x2400cf
        )
        await interaction.response.send_message(embed=embed)
        return

    await interaction.response.defer()

    try:
        subprocess.run(["docker", "start", container_id], check=True)
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        ssh_session_line = await capture_ssh_session_line(exec_cmd)
        
        if ssh_session_line:
            # Update SSH command in database
            if os.path.exists(database_file):
                with open(database_file, 'r') as f:
                    lines = f.readlines()
                with open(database_file, 'w') as f:
                    for line in lines:
                        if container_id in line:
                            parts = line.strip().split('|')
                            if len(parts) >= 3:
                                parts[2] = ssh_session_line
                                f.write('|'.join(parts) + '\n')
                        else:
                            f.write(line)
            
            # Send DM with SSH command
            dm_embed = discord.Embed(
                title="▶️ VPS Started",
                description=f"Your VPS instance `{container_name}` has been started successfully.",
                color=0x2400ff
            )
            dm_embed.add_field(
                name="🔑 SSH Connection Command",
                value=f"```{ssh_session_line}```",
                inline=False
            )
            
            try:
                await interaction.user.send(embed=dm_embed)
                
                # Public success message
                success_embed = discord.Embed(
                    title="✅ VPS Started",
                    description=f"Your VPS instance `{container_name}` has been started. Check your DMs for connection details.",
                    color=0x2400ff
                )
                await interaction.followup.send(embed=success_embed)
            except discord.Forbidden:
                # If DMs are closed
                warning_embed = discord.Embed(
                    title="⚠️ Cannot Send DM",
                    description="Your VPS has been started, but I couldn't send you a DM with the connection details. Please enable DMs from server members.",
                    color=0x2400ff
                )
                warning_embed.add_field(
                    name="🔑 SSH Connection Command",
                    value=f"```{ssh_session_line}```",
                    inline=False
                )
                await interaction.followup.send(embed=warning_embed)
        else:
            error_embed = discord.Embed(
                title="⚠️ Partial Success",
                description="VPS started, but failed to get SSH session line.",
                color=0x2400ff
            )
            await interaction.followup.send(embed=error_embed)
    except subprocess.CalledProcessError as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"Error starting VPS instance: {e}",
            color=0x2400ff
        )
        await interaction.followup.send(embed=error_embed)

async def stop_server(interaction: discord.Interaction, container_name: str):
    user = str(interaction.user)
    container_id = get_container_id_from_database(user, container_name)

    if not container_id:
        embed = discord.Embed(
            title="❌ Not Found",
            description="No instance found with that name for your user.",
            color=0x2400ff
        )
        await interaction.response.send_message(embed=embed)
        return

    await interaction.response.defer()

    try:
        subprocess.run(["docker", "stop", container_id], check=True)
        success_embed = discord.Embed(
            title="⏹️ VPS Stopped",
            description=f"Your VPS instance `{container_name}` has been stopped. You can start it again with `/start {container_name}`",
            color=0x2400ff
        )
        await interaction.followup.send(embed=success_embed)
    except subprocess.CalledProcessError as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"Failed to stop VPS instance: {str(e)}",
            color=0x2400ff
        )
        await interaction.followup.send(embed=error_embed)

async def restart_server(interaction: discord.Interaction, container_name: str):
    user = str(interaction.user)
    container_id = get_container_id_from_database(user, container_name)

    if not container_id:
        embed = discord.Embed(
            title="❌ Not Found",
            description="No instance found with that name for your user.",
            color=0x2400ff
        )
        await interaction.response.send_message(embed=embed)
        return

    await interaction.response.defer()

    try:
        subprocess.run(["docker", "restart", container_id], check=True)
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        ssh_session_line = await capture_ssh_session_line(exec_cmd)
        
        if ssh_session_line:
            # Update SSH command in database
            if os.path.exists(database_file):
                with open(database_file, 'r') as f:
                    lines = f.readlines()
                with open(database_file, 'w') as f:
                    for line in lines:
                        if container_id in line:
                            parts = line.strip().split('|')
                            if len(parts) >= 3:
                                parts[2] = ssh_session_line
                                f.write('|'.join(parts) + '\n')
                        else:
                            f.write(line)
            
            # Send DM with SSH command
            dm_embed = discord.Embed(
                title="🔄 VPS Restarted",
                description=f"Your VPS instance `{container_name}` has been restarted successfully.",
                color=0x2400ff
            )
            dm_embed.add_field(
                name="🔑 SSH Connection Command",
                value=f"```{ssh_session_line}```",
                inline=False
            )
            
            try:
                await interaction.user.send(embed=dm_embed)
                
                # Public success message
                success_embed = discord.Embed(
                    title="✅ VPS Restarted",
                    description=f"Your VPS instance `{container_name}` has been restarted. Check your DMs for connection details.",
                    color=0x2400ff
                )
                await interaction.followup.send(embed=success_embed)
            except discord.Forbidden:
                # If DMs are closed
                warning_embed = discord.Embed(
                    title="⚠️ Cannot Send DM",
                    description="Your VPS has been restarted, but I couldn't send you a DM with the connection details. Please enable DMs from server members.",
                    color=0x2400ff
                )
                warning_embed.add_field(
                    name="🔑 SSH Connection Command",
                    value=f"```{ssh_session_line}```",
                    inline=False
                )
                await interaction.followup.send(embed=warning_embed)
        else:
            error_embed = discord.Embed(
                title="⚠️ Partial Success",
                description="VPS restarted, but failed to get SSH session line.",
                color=0x2400ff
            )
            await interaction.followup.send(embed=error_embed)
    except subprocess.CalledProcessError as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"Error restarting VPS instance: {e}",
            color=0x2400ff
        )
        await interaction.followup.send(embed=error_embed)

async def capture_output(process, keyword):
    while True:
        output = await process.stdout.readline()
        if not output:
            break
        output = output.decode('utf-8').strip()
        if keyword in output:
            return output
    return None

@bot.tree.command(name="port-add", description="🔌 Adds a port forwarding rule")
@app_commands.describe(container_name="The name of the container", container_port="The port in the container")
async def port_add(interaction: discord.Interaction, container_name: str, container_port: int):
    embed = discord.Embed(
        title="🔄 Setting Up IPV4 Forwarding",
        description="Setting up port forwarding. This might take a moment...",
        color=0x2400ff
    )
    await interaction.response.send_message(embed=embed)

    public_port = generate_random_port()

    # Set up port forwarding inside the container
    command = f"ssh -o StrictHostKeyChecking=no -R {public_port}:localhost:{container_port} serveo.net -N -f"

    try:
        # Run the command in the background using Docker exec
        await asyncio.create_subprocess_exec(
            "docker", "exec", container_name, "bash", "-c", command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )

        # Respond with the port and public IP
        success_embed = discord.Embed(
            title="✅ Get IPV4 Successful",
            description=f"Your service is now accessible from the internet.",
            color=0x2400ff
        )
        success_embed.add_field(
            name="🌐 Connection Details",
            value=f"**Host:** {PUBLIC_IP}\n**Port:** {public_port}",
            inline=False
        )
        await interaction.followup.send(embed=success_embed)

    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"An unexpected error occurred: {e}",
            color=0x2400ff
        )
        await interaction.followup.send(embed=error_embed)

@bot.tree.command(name="port-http", description="🌐 Forward HTTP traffic to your container")
@app_commands.describe(container_name="The name of your container", container_port="The port inside the container to forward")
async def port_forward_website(interaction: discord.Interaction, container_name: str, container_port: int):
    embed = discord.Embed(
        title="🔄 Setting Up HTTP Forwarding",
        description="Setting up HTTP forwarding. This might take a moment...",
        color=0x2400ff
    )
    await interaction.response.send_message(embed=embed)
    
    try:
        exec_cmd = await asyncio.create_subprocess_exec(
            "docker", "exec", container_name, "ssh", "-o", "StrictHostKeyChecking=no", "-R", f"80:localhost:{container_port}", "serveo.net",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        url_line = await capture_output(exec_cmd, "Forwarding HTTP traffic from")
        
        if url_line:
            url = url_line.split(" ")[-1]
            success_embed = discord.Embed(                title="✅ HTTP Forwarding Successful",
                description=f"Your web service is now accessible from the internet.",
                color=0x2400ff
            )
            success_embed.add_field(
                name="🌐 Website URL",
                value=f"[{url}](https://{url})",
                inline=False
            )
            await interaction.followup.send(embed=success_embed)
        else:
            error_embed = discord.Embed(
                title="❌ Error",
                description="Failed to set up HTTP forwarding. Please try again later.",
                color=0x2400ff
            )
            await interaction.followup.send(embed=error_embed)
    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"An unexpected error occurred: {e}",
            color=0x2400ff
        )
        await interaction.followup.send(embed=error_embed)

@bot.tree.command(name="deploy", description="🚀 Admin: Deploy a new VPS instance")
@app_commands.describe(
    ram="RAM allocation in GB (max 100gb)",
    cpu="CPU cores (max 24)",
    target_user="Discord user ID to assign the VPS to",
    container_name="Custom container name (default: auto-generated)",
    expiry="Time until expiry (e.g. 1d, 2h, 30m, 45s, 1y, 3M)"
)
async def deploy(
    interaction: discord.Interaction, 
    ram: int = 16073727272727272827200, 
    cpu: int = 40, 
    target_user: str = None,
    container_name: str = None,
    expiry: str = None
):
    # Check if user is admin
    if interaction.user.id not in ADMIN_IDS:
        embed = discord.Embed(
            title="❌ Access Denied",
            description="You don't have permission to use this command.",
            color=0x2400ff
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Validate parameters
    if ram > 160027277272727272720:
        ram = 90002772727272727370
    if cpu > 4072727:
        cpu = 926260
    
    # Set target user
    user_id = target_user if target_user else str(interaction.user.id)
    user = target_user if target_user else str(interaction.user)
    
    # Generate container name if not provided
    if not container_name:
        username = interaction.user.name.replace(" ", "_")
        random_string = generate_random_string(8)
        container_name = f"VPS_{username}_{random_string}"
    
    # Parse expiry time
    expiry_seconds = parse_time_to_seconds(expiry)
    expiry_date = format_expiry_date(expiry_seconds) if expiry_seconds else None
    
    # Show OS selection dropdown
    embed = discord.Embed(
        title="**🖥️ Select Operating System**",
        description="** 🔍 Please select the operating system for your VPS instance **",
        color=0x2400ff
    )
    
    async def os_selected_callback(interaction, selected_os):
        await deploy_with_os(interaction, selected_os, ram, cpu, user_id, user, container_name, expiry_date)
    
    view = OSSelectView(os_selected_callback)
    await interaction.response.send_message(embed=embed, view=view)

async def deploy_with_os(interaction, os_type, ram, cpu, user_id, user, container_name, expiry_date):
    # Prepare response
    embed = discord.Embed(
        title="⚙️ Creating VM",
        description=f"**💾 RAM: {ram}GB\n**"
                    f"**🔥 CPU: {cpu} cores\n**"
                    f" 🧊**OS:** {os_type}\n"
                    f"**🧊 conatiner name: {user}\n**"
                    f"**⌚ Expiry: {expiry_date if expiry_date else 'None'}**",
        color=0x2400ff
    )
    await interaction.followup.send(embed=embed)
    
    # Select image based on OS type
    image = get_docker_image_for_os(os_type)
    
    try:
        # Create container with resource limits
        container_id = subprocess.check_output([
            "docker", "run", "-itd", 
            "--privileged", 
            "--cap-add=ALL",
            f"--memory={ram}g",
            f"--cpus={cpu}",
            "--name", container_name,
            image
        ]).strip().decode('utf-8')
    except subprocess.CalledProcessError as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"Error creating Docker container: {e}",
            color=0x2400ff
        )
        await interaction.followup.send(embed=error_embed)
        return

    try:
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_name, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"Error executing tmate in Docker container: {e}",
            color=0x2400ff
        )
        await interaction.followup.send(embed=error_embed)
        
        # Clean up container
        subprocess.run(["docker", "stop", container_name], check=False)
        subprocess.run(["docker", "rm", container_name], check=False)
        return

    ssh_session_line = await capture_ssh_session_line(exec_cmd)
    if ssh_session_line:
        # Add to database with extended information
        add_to_database(
            user, 
            container_name, 
            ssh_session_line, 
            ram_limit=ram, 
            cpu_limit=cpu, 
            creator=str(interaction.user),
            expiry=expiry_date,
            os_type=os_type_to_display_name(os_type)
        )
        
        # Create a DM embed with detailed information
        dm_embed = discord.Embed(
            description=f"**✅ VPS created successfully. Check your DM for details.**",
            color=0x2400ff
        )
        
        
        dm_embed.add_field(name="🔑 SSH Connection Command", value=f"```{ssh_session_line}```", inline=False)
        dm_embed.add_field(name="💾 RAM Allocation", value=f"{ram}GB", inline=True)
        dm_embed.add_field(name="🔥 CPU Cores", value=f"{cpu} cores", inline=True)
        dm_embed.add_field(name="🧊 Container Name", value=container_name, inline=False)
        dm_embed.add_field(name="💾 Storage", value=f"10000 GB (Shared storage)", inline=True)
        dm_embed.add_field(name="🔒 Password", value="lpnodes", inline=False)
        
        dm_embed.set_footer(text="Keep this information safe and private!")
        
        # Try to send DM to target user
        target_user_obj = await bot.fetch_user(int(user_id))
        
        try:
            await target_user_obj.send(embed=dm_embed)
            
            # Public success message
            success_embed = discord.Embed(
                title="**⛈️ VM WAS CREATE**",
                description=f"** 🎉 VPS instance has been created for <@{user_id}>. They should check their DMs for connection details.**",
                color=0x2400ff
            )
            await interaction.followup.send(embed=success_embed)
            
        except discord.Forbidden:
            # If DMs are closed
            warning_embed = discord.Embed(
                title="**🔍 Cannot Send DM**",
                description=f"**VPS has been created, but I couldn't send a DM with the connection details to <@{user_id}>. Please enable DMs from server members.**",
                color=0x2400ff
            )
            warning_embed.add_field(name="🔑 SSH Connection Command", value=f"```{ssh_session_line}```", inline=False)
            await interaction.followup.send(embed=warning_embed)
    else:
        # Clean up container if SSH session couldn't be established
        try:
            subprocess.run(["docker", "stop", container_name], check=False)
            subprocess.run(["docker", "rm", container_name], check=False)
        except Exception:
            pass
        
        error_embed = discord.Embed(
            title="❌ Deployment Failed",
            description="Failed to establish SSH session. The container has been cleaned up. Please try again.",
            color=0x2400ff
        )
        await interaction.followup.send(embed=error_embed)

def os_type_to_display_name(os_type):
    """Convert OS type to display name"""
    os_map = {
        "ubuntu": "Ubuntu 22.04",
        "debian": "Debian 12"
    }
    return os_map.get(os_type, "Unknown OS")

def get_docker_image_for_os(os_type):
    """Get Docker image name for OS type"""
    os_map = {
        "ubuntu": "ubuntu-22.04-with-tmate",
        "debian": "debian-with-tmate"
    }
    return os_map.get(os_type, "ubuntu-22.04-with-tmate")

# Tips navigation view
class TipsView(View):
    def __init__(self):
        super().__init__(timeout=300)  # 5 minute timeout
        self.current_page = 0
        self.tips = [
            {
                "title": "🔑 SSH Connection Tips",
                "description": "• Use `ssh-keygen` to create SSH keys for passwordless login\n"
                              "• Forward ports with `-L` flag: `ssh -L 8080:localhost:80 user@host`\n"
                              "• Keep connections alive with `ServerAliveInterval=60` in SSH config\n"
                              "• Use `tmux` or `screen` to keep sessions running after disconnect"
            },
            {
                "title": "🛠️ System Management",
                "description": "• Update packages regularly: `apt update && apt upgrade`\n"
                              "• Monitor resources with `htop` or `top`\n"
                              "• Check disk space with `df -h`\n"
                              "• View logs with `journalctl` or check `/var/log/`"
            },
            {
                "title": "🌐 Web Hosting Tips",
                "description": "• Install Nginx or Apache for web hosting\n"
                              "• Secure with Let's Encrypt for free SSL certificates\n"
                              "• Use PM2 to manage Node.js applications\n"
                              "• Set up proper firewall rules with `ufw`"
            },
            {
                "title": "📊 Performance Optimization",
                "description": "• Limit resource-intensive processes\n"
                              "• Use caching for web applications\n"
                              "• Configure swap space for low-memory situations\n"
                              "• Optimize database queries and indexes"
            },
            {
                "title": "🔒 Security Best Practices",
                "description": "• Change default passwords immediately\n"
                              "• Disable root SSH login\n"
                              "• Keep software updated\n"
                              "• Use `fail2ban` to prevent brute force attacks\n"
                              "• Regularly backup important data"
            }
        ]
    
    @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = (self.current_page - 1) % len(self.tips)
        await interaction.response.edit_message(embed=self.get_current_embed(), view=self)
    
    @discord.ui.button(label="▶️ Next", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = (self.current_page + 1) % len(self.tips)
        await interaction.response.edit_message(embed=self.get_current_embed(), view=self)
    
    def get_current_embed(self):
        tip = self.tips[self.current_page]
        embed = discord.Embed(
            title=tip["title"],
            description=tip["description"],
            color=0x00aaff
        )
        embed.set_footer(text=f"Tip {self.current_page + 1}/{len(self.tips)}")
        return embed

@bot.tree.command(name="tips", description="💡 Shows useful tips for managing your VPS")
async def tips_command(interaction: discord.Interaction):
    view = TipsView()
    embed = view.get_current_embed()
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="delete", description="Delete your VPS instance")
@app_commands.describe(container_name="The name of your container")
async def delete_server(interaction: discord.Interaction, container_name: str):
    user = str(interaction.user)
    container_id = get_container_id_from_database(user, container_name)

    if not container_id:
        embed = discord.Embed(
            title="❌ Not Found",
            description="No instance found with that name for your user.",
            color=0x2400ff
        )
        await interaction.response.send_message(embed=embed)
        return

    # Create confirmation dialog
    confirm_embed = discord.Embed(
        title="**⚠️ Confirm Deletion**",
        description=f"**Are you sure you want to delete VPS instance `{container_name}`? This action cannot be undone.**",
        color=0x2400ff
    )
    
    view = ConfirmView(container_id, container_name)
    await interaction.response.send_message(embed=confirm_embed, view=view)

@bot.tree.command(name="delete-all", description="🗑️ Admin: Delete all VPS instances")
async def delete_all_servers(interaction: discord.Interaction):
    # Check if user is admin
    if interaction.user.id not in ADMIN_IDS:
        embed = discord.Embed(
            title="**❌ Access Denied**",
            description="**You don't have permission to use this command.**",
            color=0x2400ff
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Get count of all containers
    containers = get_all_containers()
    
    # Create confirmation dialog
    confirm_embed = discord.Embed(
        title="**⚠️ Confirm Mass Deletion**",
        description=f"**Are you sure you want to delete ALL {len(containers)} VPS instances? This action cannot be undone.**",
        color=0x2400ff
    )
    
    view = ConfirmView(None, None, is_delete_all=True)
    await interaction.response.send_message(embed=confirm_embed, view=view)

@bot.tree.command(name="list", description="📋 List all your VPS instances")
async def list_servers(interaction: discord.Interaction):
    user = str(interaction.user)
    servers = get_user_servers(user)

    await interaction.response.defer()

    if not servers:
        embed = discord.Embed(
            title="📋 Your VPS",
            description="**You don't have any VPS instances. Use `/deploy` to create one!**",
            color=0x2400ff
        )
        await interaction.followup.send(embed=embed)
        return

    embed = discord.Embed(
        title="📋 Your VPS",
        description=f"**You have {len(servers)} VPS instance(s)**",
        color=0x2400ff
    )

    for server in servers:
        parts = server.split('|')
        container_id = parts[1]
        
        # Get container status
        try:
            container_info = subprocess.check_output(["docker", "inspect", "--format", "{{.State.Status}}", container_id]).decode().strip()
            status = "🟢 Running" if container_info == "running" else "🔴 Stopped"
        except:
            status = "🔴 Stopped"
        
        # Get resource limits and other details
        if len(parts) >= 8:
            ram_limit, cpu_limit, creator, os_type, expiry = parts[3], parts[4], parts[5], parts[6], parts[7]
            
            embed.add_field(
                name=f"🖥️ {container_id} ({status})",
                value=f"💾 **RAM:** {ram_limit}GB\n"
                      f"🔥 **CPU:** {cpu_limit} cores\n"
                      f"💾 **Storage:** 10000 GB (Shared)\n"
                      f" 🧊**OS:** {os_type}\n"
                      f"👑 **Created by:** {creator}\n"
                      f"⏱️ **Expires:** {expiry}",
                inline=False
            )
        else:
            embed.add_field(
                name=f"🖥️ {container_id} ({status})",
                value=f"💾 **RAM:** 16GB\n"
                      f"🔥 **CPU:** 40 core\n"
                      f"💾 **Storage:** 10000 GB (Shared)\n"
                      f"🧊 **OS:** Ubuntu 22.04",
                inline=False
            )

    await interaction.followup.send(embed=embed)

@bot.tree.command(name="sendvps", description="👑 Admin: Send VPS details to a user via DM")
@app_commands.describe(
    ram="RAM in GB",
    cpu="CPU cores",
    ip="IP address",
    port="Port number",
    password="VPS password",
    fullcombo="Full combo format: user@ip:port:pass",
    user="Select the user to send VPS details"
)
async def sendvps(
    interaction: discord.Interaction,
    ram: str,
    cpu: str,
    ip: str,
    port: str,
    password: str,
    fullcombo: str,
    user: discord.User
):
    # Check admin permissions
    if interaction.user.id not in ADMIN_IDS:
        embed = discord.Embed(
            title="❌ Access Denied",
            description="Only deathsaif admins can use this command.",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Create the VPS detail embed
    embed = discord.Embed(
        title="✅ VPS Created Successfully!",
        description="Here are your VPS details. Please **save them securely.**",
        color=0x2400ff
    )
    embed.add_field(name="🌐 IP", value=ip, inline=True)
    embed.add_field(name="🔌 Port", value=port, inline=True)
    embed.add_field(name="🔒 Password", value=password, inline=True)
    embed.add_field(name="🧬 Full Combo", value=f"```{fullcombo}```", inline=False)
    embed.add_field(name="💾 RAM", value=f"{ram} GB", inline=True)
    embed.add_field(name="🔥 CPU", value=f"{cpu} cores", inline=True)
    embed.set_footer(text="🔐 Safe your details | Powered by LP NODES")

    try:
        await user.send(embed=embed)
        success = discord.Embed(
            title="📨 DM Sent",
            description=f"Successfully sent VPS details to {user.mention}.",
            color=0x00ff00
        )
        await interaction.response.send_message(embed=success)
    except discord.Forbidden:
        error = discord.Embed(
            title="❌ DM Failed",
            description=f"Could not send DM to {user.mention}. They may have DMs disabled.",
            color=0xff0000
        )
        await interaction.response.send_message(embed=error)


@bot.tree.command(name="regen-ssh", description="🔄 Regenerate SSH session for your instance")
@app_commands.describe(container_name="The name of your container")
async def regen_ssh(interaction: discord.Interaction, container_name: str):
    await regen_ssh_command(interaction, container_name)

@bot.tree.command(name="start", description="▶️ Start your VPS instance")
@app_commands.describe(container_name="The name of your container")
async def start(interaction: discord.Interaction, container_name: str):
    await start_server(interaction, container_name)

@bot.tree.command(name="stop", description="⏹️ Stop your VPS instance")
@app_commands.describe(container_name="The name of your container")
async def stop(interaction: discord.Interaction, container_name: str):
    await stop_server(interaction, container_name)

@bot.tree.command(name="restart", description="🔄 Restart your VPS instance")
@app_commands.describe(container_name="The name of your container")
async def restart(interaction: discord.Interaction, container_name: str):
    await restart_server(interaction, container_name)

@bot.tree.command(name="ping", description="🏓 Check the bot's latency")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Latency: {latency}ms",
        color=0x00ff00
    )
    await interaction.response.send_message(embed=embed)

def get_invite_rewards(invite_count):
    if invite_count >= 15:
        return {"ram": 32, "cpu": 9}
    elif invite_count >= 8:
        return {"ram": 8, "cpu": 2}
    else:
        return None

def get_boost_rewards(boost_count):
    if boost_count >= 2:
        return {"ram": 31, "cpu": 4}
    else:
        return None
class RewardSelectView(View):
    def __init__(self, user: discord.Member):
        super().__init__(timeout=60)
        self.user = user
        self.add_item(Select(
            placeholder="Select your reward method",
            options=[
                discord.SelectOption(label="Invite Reward", value="invite", emoji="✉️"),
                discord.SelectOption(label="Boost Reward", value="boost", emoji="🎁")
            ]
        ))

    @discord.ui.select()
    async def select_callback(self, interaction: discord.Interaction, select: Select):
        choice = select.values[0]

        if choice == "invite":
            invites = await interaction.guild.invites()
            user_invites = sum(i.uses for i in invites if i.inviter == self.user)
            reward = get_invite_rewards(user_invites)
            if reward:
                await send_vps_request(interaction, self.user, "Invite", reward, user_invites)
            else:
                await interaction.response.send_message(f"❌ You have only **{user_invites} invites**. You need at least **8** to claim.", ephemeral=True)

        elif choice == "boost":
            boost_count = self.user.premium_since is not None and interaction.guild.premium_subscriber_count or 0
            reward = get_boost_rewards(boost_count)
            if reward:
                await send_vps_request(interaction, self.user, "Boost", reward, boost_count)
            else:
                await interaction.response.send_message(f"❌ You need at least **2 boosts** to claim. Current: {boost_count}", ephemeral=True)
@bot.tree.command(name="create", description="🎁 Request a VPS via Invite or Boost rewards")
async def create(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("❌ You must use this in a server.", ephemeral=True)
        return

    view = RewardSelectView(interaction.user)
    embed = discord.Embed(
        title="🎉 VPS Reward Selection",
        description="Please select your reward method below.",
        color=0x2400ff
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def send_vps_request(interaction, user, method, reward, count):
    channel = bot.get_channel(1390545538239299608)
    if not channel:
        await interaction.response.send_message("❌ VPS channel not found.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🚀 VPS Request Submitted",
        description=f"User: {user.mention}\nMethod: {method} Reward",
        color=0x2400ff
    )
    embed.add_field(name="📊 RAM", value=f"{reward['ram']} GB", inline=True)
    embed.add_field(name="🔥 CPU", value=f"{reward.get('cpu', 2)} cores", inline=True)
    embed.set_footer(text=f"{count} {'invites' if method == 'Invite' else 'boosts'}")
    await channel.send(embed=embed)
    await interaction.response.send_message("✅ Your VPS request has been sent for approval!", ephemeral=True)

@bot.tree.command(name="help", description="❓ Shows the help message")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="**🌟 VPS Bot Help**",
        description="** Here are all the available commands:**",
        color=0x00aaff
    )
    
    # User commands
    embed.add_field(
        name="📋 User Commands",
        value="Commands available to all users:",
        inline=False
    )
    embed.add_field(name="/start <container_name>", value="Start your VPS instance", inline=True)
    embed.add_field(name="/stop <container_name>", value="Stop your VPS instance", inline=True)
    embed.add_field(name="/restart <container_name>", value="Restart your VPS instance", inline=True)
    embed.add_field(name="/regen-ssh <container_name>", value="Regenerate SSH credentials", inline=True)
    embed.add_field(name="/list", value="List all your VPS instances", inline=True)
    embed.add_field(name="/delete <container_name>", value="Delete your VPS instance", inline=True)
    embed.add_field(name="/port-add <container_name> <port>", value="Forward a port", inline=True)
    embed.add_field(name="/port-http <container_name> <port>", value="Forward HTTP traffic", inline=True)
    embed.add_field(name="/ping", value="Check bot latency", inline=True)
    
    # Admin commands
    if interaction.user.id in ADMIN_IDS:
        embed.add_field(
            name="👑 Admin Commands",
            value="Commands available only to admins:",
            inline=False
        )
        embed.add_field(name="/deploy", value="Deploy a new VPS with custom settings", inline=True)
        embed.add_field(name="/node", value="View system resource usage", inline=True)
        embed.add_field(name="/nodedmin", value="List all VPS instances with details", inline=True)
        embed.add_field(name="/delete-all", value="Delete all VPS instances", inline=True)
    
    await interaction.response.send_message(embed=embed)

bot.run(MTM5NzgyOTk1NTk1MzQ5NjEwNA.GtqEJF._ubTBv6ZvjxGgOCXwW5CPgKH2OaDCaDD9dPVMM)
