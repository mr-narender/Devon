pyinstaller devon_agent/__main__.py --hidden-import=tiktoken_ext.openai_public --hidden-import=tiktoken_ext --clean --onefile --collect-all  litellm --hidden-import=aiosqlite --hidden-import=dotenv
mv dist/__main__ newelectron/src/backend/devon_agent
