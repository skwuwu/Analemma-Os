# Local development environment setup
# Add this to your shell profile or run before development

# Set PYTHONPATH to match Lambda deployment structure
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Optional: Add to PATH for convenience
export PATH="${PATH}:$(pwd)/scripts"

echo "✅ Local development environment configured"
echo "PYTHONPATH: $PYTHONPATH"