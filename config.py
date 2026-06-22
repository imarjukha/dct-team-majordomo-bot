import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
HR_GROUP_ID = int(os.environ["HR_GROUP_ID"])
SUPERADMIN_ID = int(os.environ.get("SUPERADMIN_ID", "120515403"))
