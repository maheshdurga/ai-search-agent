from flask import Flask, render_template, request, redirect, url_for, session
import os
from agent import run_agent, image_to_code, psd_to_png, figma_to_png, refine_code

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-this")

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route("/", methods=["GET"])
def home():
    # .pop() reads the value ONCE then deletes it from the session -
    # this is what makes a plain refresh show a clean/empty page afterward
    answer = session.pop("answer", None)
    code = session.pop("code", None)
    return render_template("index.html", answer=answer, code=code)

@app.route("/ask", methods=["POST"])
def ask():
    question = request.form.get("question")
    try:
        answer = run_agent(question)
    except Exception as e:
        answer = f"Error occurred: {str(e)}"
    session["answer"] = answer
    return redirect(url_for("home"))

@app.route("/image-to-code", methods=["POST"])
def image_to_code_route():
    file = request.files.get("image")
    try:
        if file and file.filename:
            path = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(path)

            if file.filename.lower().endswith(".psd"):
                path = psd_to_png(path)

            code = image_to_code(path)
        else:
            code = "No file was uploaded. Please choose an image or PSD file."
    except Exception as e:
        code = f"Error occurred: {str(e)}"
    session["code"] = code
    return redirect(url_for("home"))

@app.route("/figma-to-code", methods=["POST"])
def figma_to_code_route():
    figma_url = request.form.get("figma_url")
    figma_token = request.form.get("figma_token")
    try:
        png_path = figma_to_png(figma_url, figma_token)
        code = image_to_code(png_path)
    except Exception as e:
        code = f"Error occurred: {str(e)}"
    session["code"] = code
    return redirect(url_for("home"))

@app.route("/refine-code", methods=["POST"])
def refine_code_route():
    previous_code = request.form.get("previous_code")
    feedback = request.form.get("feedback")
    try:
        code = refine_code(previous_code, feedback)
    except Exception as e:
        code = f"Error occurred: {str(e)}\n\n{previous_code}"
    session["code"] = code
    return redirect(url_for("home"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)