import subprocess

# Run the dir command
result = subprocess.run(['dir'], shell=True, capture_output=True, text=True)

# Print the output
print(result.stdout)

# Print any errors
if result.stderr:
    print("Errors:", result.stderr)