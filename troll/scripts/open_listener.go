// Local helper: listens on 127.0.0.1:<port> for a URL and opens it in the
// operator's default browser.
//
// Paired with bot_tui's space/o keys (see app.py's _open_via_local_listener)
// via troll-tui's reverse SSH tunnel (-R <port>:localhost:<port>) -- bot_tui
// runs on the VPS with no browser of its own, so webbrowser.open() there is
// always a no-op. This runs on the operator's actual machine instead; bot_tui
// sends the URL down the tunnel to it, and this is what actually pops the
// Firefox tab. troll-tui starts this (via `go run`), troll-down stops it
// (see ~/.zshrc).
//
// Run directly: go run open_listener.go [port] (default 8901).
package main

import (
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"strconv"
	"strings"
)

const defaultPort = 8901

func main() {
	port := defaultPort
	if len(os.Args) > 1 {
		p, err := strconv.Atoi(os.Args[1])
		if err != nil {
			fmt.Fprintf(os.Stderr, "invalid port %q: %v\n", os.Args[1], err)
			os.Exit(1)
		}
		port = p
	}

	addr := fmt.Sprintf("127.0.0.1:%d", port)
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "listen on %s failed: %v\n", addr, err)
		os.Exit(1)
	}
	defer ln.Close()
	fmt.Printf("troll open-listener on %s\n", addr)

	for {
		conn, err := ln.Accept()
		if err != nil {
			fmt.Fprintf(os.Stderr, "accept failed: %v\n", err)
			continue
		}
		handle(conn)
	}
}

func handle(conn net.Conn) {
	defer conn.Close()
	body, err := io.ReadAll(conn)
	if err != nil {
		fmt.Fprintf(os.Stderr, "read failed: %v\n", err)
		return
	}
	url := strings.TrimSpace(string(body))

	// Only ever asked to open the dashboard's own chart URLs, but this port is
	// reachable from anything that can reach the VPS's own loopback -- refuse
	// anything that isn't a plain http(s) URL rather than shelling out to
	// xdg-open with an arbitrary string.
	if !strings.HasPrefix(url, "http://") && !strings.HasPrefix(url, "https://") {
		fmt.Printf("refused non-http(s) url: %q\n", url)
		return
	}

	// xdg-open respects the user's configured default browser (Firefox here),
	// same behavior Python's webbrowser.open() delegates to on Linux.
	if err := exec.Command("xdg-open", url).Start(); err != nil {
		fmt.Fprintf(os.Stderr, "xdg-open failed for %s: %v\n", url, err)
	}
}
