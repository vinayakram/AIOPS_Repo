import click
from aiops_sdk.cli.instrument import instrument_cmd


@click.group()
def cli():
    """AIops SDK CLI — instrument and manage telemetry."""


cli.add_command(instrument_cmd, name="instrument")
