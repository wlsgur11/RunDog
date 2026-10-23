#!/bin/zsh
# RunDog.app을 빌드해서 /Applications에 설치
set -e
cd "$(dirname "$0")"

APP=build/RunDog.app
rm -rf build
mkdir -p "$APP/Contents/MacOS"
cp Info.plist "$APP/Contents/"
swiftc -O main.swift -o "$APP/Contents/MacOS/RunDog"
codesign --force --sign - "$APP"

pkill -x RunDog || true
rm -rf /Applications/RunDog.app
cp -R "$APP" /Applications/
open /Applications/RunDog.app
echo "설치 완료: /Applications/RunDog.app"
