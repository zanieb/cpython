#!/usr/bin/env bash
# Build x86_64 libraries on an Apple silicon host.
set -euxo pipefail

test "$(uname -sm)" = "Darwin arm64"
mkdir -p "$1" "$2"
prefix=$(cd "$1" && pwd -P)
build_dir=$(cd "$2" && pwd -P)
source_dir=$(cd "$(dirname "$0")/../.." && pwd -P)
build_triple=$("$source_dir/config.guess")
host_triple=x86_64-apple-darwin
build_cc=${CC:-clang}

SDKROOT=$(xcrun --show-sdk-path)
export SDKROOT
export MACOSX_DEPLOYMENT_TARGET=26.0
export CC="$build_cc"
export CFLAGS="-arch x86_64 -O2 -fPIC"
export CPPFLAGS="-arch x86_64 -I$prefix/include"
export LDFLAGS="-arch x86_64 -L$prefix/lib"
export PKG_CONFIG_PATH=""
export PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig:$prefix/share/pkgconfig"

fetch() {
    local name=$1 url=$2 checksum=$3
    curl --fail --location --retry 3 "$url" -o "$build_dir/$name.tar.gz"
    printf '%s  %s\n' "$checksum" "$build_dir/$name.tar.gz" | shasum --algorithm 256 --check
    mkdir -p "$build_dir/$name"
    tar -xzf "$build_dir/$name.tar.gz" -C "$build_dir/$name" --strip-components=1
}

build_autoconf() (
    cd "$build_dir/$1"
    shift
    ./configure --build="$build_triple" --host="$host_triple" \
        --prefix="$prefix" --disable-shared "$@"
    make --jobs 4
    make install
)

# Build these dependencies as static libraries for the Python extensions.
fetch zlib \
    https://github.com/madler/zlib/releases/download/v1.3.2/zlib-1.3.2.tar.gz \
    bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16
(
    cd "$build_dir/zlib"
    CHOST="$host_triple" ./configure --prefix="$prefix" --static
    make --jobs 4
    make install
)

fetch gdbm \
    https://ftp.gnu.org/gnu/gdbm/gdbm-1.26.tar.gz \
    6a24504a14de4a744103dcb936be976df6fbe88ccff26065e54c1c47946f4a5e
build_autoconf gdbm --disable-nls --without-readline

fetch mpdecimal \
    https://www.bytereef.org/software/mpdecimal/releases/mpdecimal-4.0.1.tar.gz \
    96d33abb4bb0070c7be0fed4246cd38416188325f820468214471938545b1ac8
build_autoconf mpdecimal --disable-cxx

fetch openssl \
    https://github.com/openssl/openssl/releases/download/openssl-3.5.9/openssl-3.5.9.tar.gz \
    603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a
(
    cd "$build_dir/openssl"
    ./Configure darwin64-x86_64-cc --prefix="$prefix" --libdir=lib \
        --openssldir=/etc/ssl no-shared no-tests
    make --jobs 4 build_sw
    make install_sw
)

fetch readline \
    https://ftp.gnu.org/gnu/readline/readline-8.3.tar.gz \
    fe5383204467828cd495ee8d1d3c037a7eba1389c22bc6a041f627976f9061cc
build_autoconf readline --with-curses

fetch sqlite \
    https://www.sqlite.org/2026/sqlite-autoconf-3530400.tar.gz \
    0e9483900e92cd5de8fd48d16bf9200145a61f7fd5be542a5ac81d8a9516eb9c
(
    cd "$build_dir/sqlite"
    unset CXX
    CC_FOR_BUILD="$build_cc" \
    CFLAGS="$CFLAGS -DSQLITE_ENABLE_FTS3 -DSQLITE_ENABLE_FTS3_PARENTHESIS -DSQLITE_ENABLE_FTS4 -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_RTREE" \
    ./configure --build="$build_triple" --host="$host_triple" \
        --prefix="$prefix" --disable-shared --disable-readline
    make --jobs 4 libsqlite3.a
    make install-lib install-headers install-pc
)

fetch xz \
    https://github.com/tukaani-project/xz/releases/download/v5.8.4/xz-5.8.4.tar.gz \
    0014c7886930454fe8bd4228665b51af55eeae560ea135c9c4cd33f55b2591d9
build_autoconf xz --disable-xz --disable-xzdec --disable-lzmadec \
    --disable-lzmainfo --disable-lzma-links --disable-scripts

fetch zstd \
    https://github.com/python/cpython-source-deps/archive/refs/tags/zstd-1.5.7.tar.gz \
    f24b52470d12f466e9fa4fcc94e6c530625ada51d7b36de7fdc6ed7e6f499c8e
(
    cd "$build_dir/zstd/lib"
    make --jobs 4 libzstd.a CC="$CC" CFLAGS="$CFLAGS -DZSTD_MULTITHREAD"
    make PREFIX="$prefix" MT=1 install-static install-includes install-pc
)

# Tcl/Tk also needs its shared libraries and scripts at runtime.
fetch tcl \
    https://prdownloads.sourceforge.net/tcl/tcl9.0.4-src.tar.gz \
    d0aed49230bc02a65c1e0229e65f34590a4b037ec40d546f32573b467f7551ea
(
    cd "$build_dir/tcl/unix"
    ./configure --build="$build_triple" --host="$host_triple" \
        --prefix="$prefix" --enable-shared --enable-threads --disable-zipfs
    make --jobs 4 binaries libraries DYLIB_INSTALL_DIR="$prefix/lib"
    make install-binaries install-libraries install-headers install-tzdata install-msgs \
        DYLIB_INSTALL_DIR="$prefix/lib"
)

fetch tk \
    https://prdownloads.sourceforge.net/tcl/tk9.0.4-src.tar.gz \
    d7a146d2917eb8b5cc95276dbf0e3d03c7464d2b19c1675357857c989301dbb4
(
    cd "$build_dir/tk/unix"
    ./configure --build="$build_triple" --host="$host_triple" \
        --prefix="$prefix" --with-tcl="$prefix/lib" \
        --enable-aqua --without-x --enable-shared --enable-threads --disable-zipfs
    make --jobs 4 DYLIB_INSTALL_DIR="$prefix/lib"
    make install-binaries install-libraries install-headers DYLIB_INSTALL_DIR="$prefix/lib"
)

while IFS= read -r library; do
    test "$(lipo -archs "$library")" = x86_64
done < <(find "$prefix/lib" -type f \( -name '*.a' -o -name '*.dylib' \))
