from fastapi import FastAPI

app = FastAPI(title="VeriGate")


@app.get("/health")
def health():
    return {"status": "healthy", "project": "VeriGate"}
