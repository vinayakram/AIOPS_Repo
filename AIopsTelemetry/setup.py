"""
Package setup configuration for the aiops-sdk Python library.
Declares the package metadata, version, dependency requirements for langchain-core,
requests, and click, and registers the aiops CLI entry point for instrumentation commands.
Requires Python 3.11 or later and discovers all aiops_sdk sub-packages automatically.

aiops-sdk Pythonライブラリのパッケージセットアップ設定。
パッケージメタデータ、バージョン、langchain-core・requests・clickの依存要件を宣言する。
計装コマンド用のaiops CLIエントリーポイントを登録し、aiops_sdkサブパッケージを自動検出する。
Python 3.11以降が必要で、find_packagesによりaiops_sdk配下を全て対象とする。
"""
from setuptools import setup, find_packages

setup(
    name="aiops-sdk",
    version="1.0.0",
    packages=find_packages(include=["aiops_sdk", "aiops_sdk.*"]),
    install_requires=[
        "langchain-core>=0.2.0",
        "requests>=2.32.0",
        "click>=8.1.0",
    ],
    entry_points={
        "console_scripts": [
            "aiops=aiops_sdk.cli:cli",
        ],
    },
    python_requires=">=3.11",
)
