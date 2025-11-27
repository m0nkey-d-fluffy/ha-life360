#!/usr/bin/env python3
"""Check if curl_cffi is installed and provide installation instructions."""

import sys
import subprocess

def check_curl_cffi():
    """Check if curl_cffi is installed."""
    try:
        import curl_cffi
        print(f"✅ curl_cffi is installed (version: {curl_cffi.__version__})")
        return True
    except ImportError:
        print("❌ curl_cffi is NOT installed")
        return False

def install_curl_cffi():
    """Attempt to install curl_cffi."""
    print("\n📦 Installing curl_cffi...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "curl_cffi>=0.5.0"])
        print("✅ curl_cffi installed successfully!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Installation failed: {e}")
        return False

def main():
    """Main entry point."""
    print("🔍 Checking curl_cffi installation for Life360 integration...\n")

    if check_curl_cffi():
        print("\n✅ All dependencies are installed!")
        sys.exit(0)
    else:
        print("\n⚠️  curl_cffi is required for Tile/Jiobit device names")
        print("\nTo install curl_cffi in Home Assistant:")
        print("1. Use the Terminal add-on or SSH into your HA container")
        print("2. Run: pip3 install curl_cffi")
        print("3. Restart Home Assistant")
        print("\nOr try automatic installation below...")

        response = input("\nAttempt automatic installation? (y/n): ")
        if response.lower() == 'y':
            if install_curl_cffi():
                print("\n✅ Installation complete! Please restart Home Assistant.")
                sys.exit(0)
            else:
                print("\n❌ Automatic installation failed. Please install manually.")
                sys.exit(1)
        else:
            print("\n❌ Skipping installation. Tile/Jiobit device names will not work.")
            sys.exit(1)

if __name__ == "__main__":
    main()
