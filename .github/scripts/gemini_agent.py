import os
import json
from google import genai

# 1. Setup Gemini using the new SDK
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL_ID = 'gemini-3.8-flash'

# 2. Read Context Files
def read_file(path):
    try:
        with open(path, 'r') as f: return f.read()
    except FileNotFoundError:
        return ""

readme = read_file("README.md")
agents = read_file("AGENTS.md")
tasks = read_file("TASKS.md")

# 3. Formulate the Prompt
prompt = f"""
You are an autonomous agent building the pwnagotchi-redux repo.
Here is the context:
README.md: {readme}
AGENTS.md: {agents}
TASKS.md: {tasks}

INSTRUCTIONS:
1. Adhere strictly to AGENTS.md rules.
2. Pick the lowest-numbered open task in TASKS.md Phase 1.
3. Write the implementation code and the pytest no-hardware unit tests.

OUTPUT FORMAT:
You MUST output a raw JSON object (no markdown formatting, no backticks). The JSON must have this exact structure:
{{
  "branch_name": "gemini/task-name",
  "files": [
    {{      "path": "src/example.py",
      "content": "print('hello world')"
    }}
  ]
}}
"""

# 4. Generate and Parse
print(f"Asking Gemini ({MODEL_ID}) to process the task...")
response = client.models.generate_content(
    model=MODEL_ID,
    contents=prompt
)
raw_text = response.text.strip()

# Strip markdown code blocks if the model accidentally includes them
if raw_text.startswith("```json"):
    raw_text = raw_text[7:-3]
elif raw_text.startswith("```"):
    raw_text = raw_text[3:-3]

try:
    data = json.loads(raw_text.strip())
    
    # 5. Write the Branch Name for the bash script to pick up
    with open(".gemini_branch_name", "w") as f:
        f.write(data["branch_name"])
        
    # 6. Write the Files to Disk
    for file_obj in data["files"]:
        path = file_obj["path"]
        content = file_obj["content"]
        
        # Ensure directories exist
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        print(f"Created/Modified: {path}")

    print("Agent execution complete. Files written successfully.")

except Exception as e:
    print(f"Error parsing Gemini output. Response was: {raw_text}")
    raise e
