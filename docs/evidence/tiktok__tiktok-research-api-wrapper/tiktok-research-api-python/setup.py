
from setuptools import setup, find_packages

from pathlib import Path
this_directory = Path(__file__).parent
long_description = (this_directory / "readme.MD").read_text()

setup(
    name="TikTokResearchApi",
    version="1.0.4",
    description="A Python package for interacting with the TikTok Research API",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author_email="",
    url="",
    packages=find_packages(exclude=["tests", "examples"]),
    install_requires=[
        "requests>=2.20.0",
    ],
    python_requires=">=3.6",
)