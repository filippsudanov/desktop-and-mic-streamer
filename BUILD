GStreamer NDI plugin — build & lib/ setup
==========================================

Assumes the NDI SDK folder is present in the project directory as
"NDI SDK for Linux" (downloaded from https://www.ndi.tv/sdk/).

1. Install Rust toolchain
--------------------------
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"

2. Install system build dependencies
--------------------------------------
sudo apt install libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
                 pkg-config build-essential

3. Clone gst-plugins-rs
------------------------
git clone https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs

4. Build the NDI plugin
------------------------
export NDI_SDK_DIR="$(pwd)/NDI SDK for Linux"
cd gst-plugins-rs
cargo build -p gst-plugin-ndi --release
cd ..

5. Set up lib/
--------------
mkdir -p lib

# GStreamer NDI plugin (built above)
cp gst-plugins-rs/target/release/libgstndi.so lib/

# NDI runtime — x86_64 only; adjust arch dir for other targets:
#   aarch64-rpi4-linux-gnueabi, arm-rpi4-linux-gnueabihf, i686-linux-gnu, …
cp "NDI SDK for Linux/lib/x86_64-linux-gnu/libndi.so.6.3.1" lib/
ln -sf libndi.so.6.3.1 lib/libndi.so.6
ln -sf libndi.so.6.3.1 lib/libndi.so

6. Verify
---------
GST_PLUGIN_PATH=lib gst-inspect-1.0 ndisink

Expected output starts with:
    Factory Details:
      Long-name    NewTek NDI Sink
      ...

7. Deploy
---------
Archive everything except the SDK and build tree:

tar --exclude='./NDI SDK for Linux' \
    --exclude='./gst-plugins-rs' \
    -czf ndi_streamer.tar.gz .

On the target machine, unpack and run:
    tar -xzf ndi_streamer.tar.gz
    ./run.sh
