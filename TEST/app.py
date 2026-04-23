import subprocess

def main():
    try:
        # Run the dir command
        result = subprocess.run(['dir'], shell=True, capture_output=True, text=True, check=True)
        print("Directory listing:")
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error running dir command: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")

if __name__ == "__main__":
    main()