#!/usr/bin/env bash
# 检查系统是否安装了中文字体（CJK fonts），导出 PPTX 前必须确认。
#
# 用法:
#   bash check-fonts.sh          # 检查并给出安装指引
#   bash check-fonts.sh --install # 检查，若缺失则自动安装（需 sudo 权限）
#
# 背景:
#   导出工具使用 Playwright (headless Chromium) 解析 HTML 布局。
#   如果系统缺少中文字体，Chromium 会用英文 fallback 字体渲染中文字符，
#   导致 getBoundingClientRect() 返回的宽度与预期不符，
#   flex/grid 布局中的卡片宽度分配异常、文本溢出容器。

set -euo pipefail

AUTO_INSTALL=false
if [[ "${1:-}" == "--install" ]]; then
  AUTO_INSTALL=true
fi

check_cjk_fonts() {
  if ! command -v fc-list &>/dev/null; then
    echo "fc-list 未找到，无法检测字体（macOS/Windows 通常自带中文字体，可忽略）"
    return 2
  fi

  local count
  count=$(fc-list :lang=zh 2>/dev/null | wc -l | tr -d ' ')

  if [[ "$count" -gt 0 ]]; then
    echo "[OK] 已安装 ${count} 个中文字体"
    return 0
  else
    echo "[FAIL] 未检测到中文字体"
    return 1
  fi
}

detect_os() {
  if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    case "$ID" in
      ubuntu|debian|linuxmint|pop) echo "debian" ;;
      centos|rhel|fedora|rocky|alma) echo "rhel" ;;
      alpine) echo "alpine" ;;
      arch|manjaro) echo "arch" ;;
      *) echo "unknown-linux" ;;
    esac
  elif [[ "$(uname)" == "Darwin" ]]; then
    echo "macos"
  else
    echo "unknown"
  fi
}

install_fonts() {
  local os
  os=$(detect_os)

  case "$os" in
    debian)
      echo "正在安装 fonts-noto-cjk ..."
      sudo apt-get update -qq && sudo apt-get install -y --no-install-recommends fonts-noto-cjk
      sudo fc-cache -fv
      ;;
    rhel)
      echo "正在安装 google-noto-sans-cjk-ttc-fonts ..."
      sudo yum install -y google-noto-sans-cjk-ttc-fonts
      sudo fc-cache -fv
      ;;
    alpine)
      echo "正在安装 font-noto-cjk ..."
      sudo apk add --no-cache font-noto-cjk
      sudo fc-cache -fv
      ;;
    arch)
      echo "正在安装 noto-fonts-cjk ..."
      sudo pacman -S --noconfirm noto-fonts-cjk
      sudo fc-cache -fv
      ;;
    macos)
      echo "[OK] macOS 自带中文字体（PingFang SC），无需额外安装"
      return 0
      ;;
    *)
      echo "[FAIL] 未识别的操作系统，请手动安装 Noto Sans CJK 字体"
      echo "参考: https://github.com/googlefonts/noto-cjk"
      return 1
      ;;
  esac

  echo "[OK] 字体安装完成"
}

print_install_guide() {
  local os
  os=$(detect_os)

  echo ""
  echo "请根据你的系统手动安装中文字体："
  echo ""

  case "$os" in
    debian)
      echo "  sudo apt-get update && sudo apt-get install -y fonts-noto-cjk && fc-cache -fv"
      ;;
    rhel)
      echo "  sudo yum install -y google-noto-sans-cjk-ttc-fonts && fc-cache -fv"
      ;;
    alpine)
      echo "  sudo apk add --no-cache font-noto-cjk && fc-cache -fv"
      ;;
    arch)
      echo "  sudo pacman -S noto-fonts-cjk && fc-cache -fv"
      ;;
    macos)
      echo "  macOS 自带中文字体，如果仍有问题请检查 Playwright 是否正确安装"
      ;;
    *)
      echo "  请安装 Noto Sans CJK: https://github.com/googlefonts/noto-cjk"
      ;;
  esac

  echo ""
  echo "或者重新运行此脚本并加上 --install 参数自动安装："
  echo "  bash $0 --install"
}

# ---- 主流程 ----

if check_cjk_fonts; then
  exit 0
fi

exit_code=$?

if [[ "$exit_code" -eq 2 ]]; then
  exit 0
fi

if [[ "$AUTO_INSTALL" == true ]]; then
  echo ""
  install_fonts
  echo ""
  echo "验证安装结果："
  check_cjk_fonts
else
  print_install_guide
  exit 1
fi
