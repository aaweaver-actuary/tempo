"""Register the existing canonical prefix operations for command composition."""
from .command_gateway import register_command
from .services.canonical_prefix import preview_command, save_prefix

register_command("repertoire.canonical_prefix.preview", preview_command)
register_command("repertoire.canonical_prefix.save", save_prefix)
