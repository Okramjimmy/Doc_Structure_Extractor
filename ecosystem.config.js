module.exports = {
  apps: [
    {
      name: "doc-structure-extractor",
      script: "/home/ubuntu/miniconda3/envs/doc_ext/bin/uvicorn",
      args: "app.main:app --reload --reload-exclude uploads --reload-exclude output --reload-exclude jobs.db* --host 0.0.0.0 --port 8001",
      cwd: "./",
      interpreter: "none",
      watch: false, // Uvicorn --reload handles auto-reloading, so PM2 watch is disabled
      autorestart: true,
      max_memory_restart: "1G",
      env: {
        DEBUG: "false",
        PYTHONUNBUFFERED: "1"
      }
    }
  ]
};
