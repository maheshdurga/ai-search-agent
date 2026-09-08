import os
import json
import base64
import re
import requests
from io import BytesIO
from PIL import Image
from psd_tools import PSDImage
from dotenv import load_dotenv
from groq import Groq, BadRequestError
from serpapi import GoogleSearch

load_dotenv()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SERPAPI_KEY = os.environ.get("SERPAPI_KEY")

client = Groq(api_key=GROQ_API_KEY)

# ---------------- SEARCH AGENT ----------------

def search_web(query):
    params = {"q": query, "api_key": SERPAPI_KEY, "num": 5}
    search = GoogleSearch(params)
    results = search.get_dict()
    snippets = []
    for result in results.get("organic_results", []):
        snippets.append(f"{result.get('title','')}: {result.get('snippet','')} ({result.get('link','')})")
    return "\n".join(snippets) if snippets else "No results found."

tools = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search Google for current, real-time information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"}
                },
                "required": ["query"]
            }
        }
    }
]

def run_agent(user_question):
    messages = [
        {
            "role": "system",
            "content": (
                "You only have access to one tool: search_web, which takes a single 'query' string argument. "
                "Never call any other tool. "
                "Search at most 2 times total. After your second search, you MUST stop searching and give "
                "your final answer as plain text using whatever information you have gathered."
            )
        },
        {"role": "user", "content": user_question}
    ]

    max_turns = 8
    for _ in range(max_turns):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                max_tokens=1000,
                tools=tools,
                messages=messages
            )
        except BadRequestError as e:
            print(f"[Tool call error, retrying: {e}]")
            messages.append({
                "role": "user",
                "content": "Your last tool call was invalid. Only call search_web with a single 'query' string argument, or answer directly if you already have enough information."
            })
            continue

        response_message = response.choices[0].message

        if response_message.tool_calls:
            messages.append(response_message)
            for tool_call in response_message.tool_calls:
                if tool_call.function.name == "search_web":
                    try:
                        args = json.loads(tool_call.function.arguments)
                        query = args["query"]
                        print(f"[Searching for: {query}]")
                        result = search_web(query)
                    except (KeyError, json.JSONDecodeError):
                        result = "Error: invalid arguments. Must provide a 'query' string."

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result
                    })
        else:
            return response_message.content

    return "Sorry, I couldn't complete this after several attempts. Please try rephrasing your question."


# ---------------- IMAGE-TO-CODE FEATURE ----------------

def encode_image(image_path):
    """Reads a local image file, resizes it if too large, and converts it to base64.
    Always outputs as JPEG to keep file size manageable for the API."""
    img = Image.open(image_path)

    # Convert to RGB if needed (handles PNGs with transparency, PSD exports, etc.)
    if img.mode != "RGB":
        img = img.convert("RGB")

    # Resize if too large - cap longest side at 1568px (plenty for the AI to read clearly)
    max_dimension = 1568
    if max(img.size) > max_dimension:
        ratio = max_dimension / max(img.size)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")

def image_to_code(image_path):
    """Sends an image to a vision-capable Groq model and asks it to recreate it as HTML/CSS/JS."""
    if not os.path.exists(image_path):
        return "Error: file not found. Check the path and try again."

    base64_image = encode_image(image_path)
    mime_type = "image/jpeg"  # always JPEG now, since encode_image() converts to it

    response = client.chat.completions.create(
        model="qwen/qwen3.6-27b",
        max_tokens=8000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Look at this UI design image and recreate it as a single, complete HTML file "
                            "with embedded CSS and JavaScript. Match the layout, colors, spacing, fonts, and "
                            "text as closely as possible. Add simple hover/interaction effects where it makes "
                            "sense. Output ONLY the raw HTML code, no explanation, no markdown code fences."
                        )
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{base64_image}"
                        }
                    }
                ]
            }
        ]
    )

    code = response.choices[0].message.content
    finish_reason = response.choices[0].finish_reason

    if finish_reason == "length":
        code += "\n\n<!-- WARNING: Output was cut off because it exceeded the token limit. -->"

    return code


# ---------------- PSD SUPPORT ----------------

def psd_to_png(psd_path):
    """Converts an uploaded .psd file into a flattened PNG image."""
    psd = PSDImage.open(psd_path)
    image = psd.composite()  # flattens all layers into one image
    png_path = psd_path.rsplit(".", 1)[0] + "_converted.png"
    image.save(png_path)
    return png_path


# ---------------- FIGMA SUPPORT ----------------

def parse_figma_url(figma_url):
    """Extracts the file key and node id from a Figma share link."""
    file_key_match = re.search(r"figma\.com/(?:file|design)/([a-zA-Z0-9]+)", figma_url)
    node_id_match = re.search(r"node-id=([0-9]+[-:][0-9]+)", figma_url)

    file_key = file_key_match.group(1) if file_key_match else None
    node_id = node_id_match.group(1).replace("-", ":") if node_id_match else None
    return file_key, node_id

def figma_to_png(figma_url, figma_token, save_dir="uploads"):
    """Fetches a rendered PNG of a Figma frame using the Figma API."""
    file_key, node_id = parse_figma_url(figma_url)
    if not file_key or not node_id:
        raise ValueError("Could not read file key or node id from that Figma URL. Make sure you copied the link while a specific frame/node is selected.")

    response = requests.get(
        f"https://api.figma.com/v1/images/{file_key}",
        headers={"X-Figma-Token": figma_token},
        params={"ids": node_id, "format": "png"}
    )
    data = response.json()

    if "images" not in data or not data["images"]:
        raise ValueError(f"Figma API error: {data}")

    image_url = list(data["images"].values())[0]
    if not image_url:
        raise ValueError("Figma returned an empty image URL. Check your token and node access.")

    image_response = requests.get(image_url)
    png_path = os.path.join(save_dir, "figma_export.png")
    with open(png_path, "wb") as f:
        f.write(image_response.content)

    return png_path


# ---------------- REFINE CODE (FEEDBACK LOOP) ----------------

def refine_code(previous_code, feedback):
    """Takes existing generated code + user feedback, and returns an updated version."""
    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        max_tokens=8000,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are editing an existing HTML file (with embedded CSS and JS). "
                    "The user will give you the current code and a change they want. "
                    "Apply ONLY the requested change, keep everything else the same. "
                    "Output ONLY the full updated HTML code, no explanation, no markdown code fences."
                )
            },
            {
                "role": "user",
                "content": f"Here is the current code:\n\n{previous_code}\n\nRequested change: {feedback}"
            }
        ]
    )
    return response.choices[0].message.content


# ---------------- MAIN MENU (only runs when you do `python agent.py` directly) ----------------

if __name__ == "__main__":
    print("What do you want to do?")
    print("1. Ask a question (web search agent)")
    print("2. Convert an image to HTML/CSS/JS code")
    choice = input("Enter 1 or 2: ").strip()

    if choice == "1":
        question = input("Ask me anything: ")
        answer = run_agent(question)
        print("\nAgent's Answer:\n", answer)

    elif choice == "2":
        image_path = input("Enter the full path to your image file: ").strip().strip('"')
        print("[Analyzing image and generating code, please wait...]")
        code = image_to_code(image_path)

        output_file = "generated_output.html"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(code)

        print(f"\nDone! Code saved to: {output_file}")
        print("Open that file in your browser or VS Code to see the result.")

    else:
        print("Invalid choice. Please enter 1 or 2.")