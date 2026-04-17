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
