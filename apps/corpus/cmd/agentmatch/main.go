package main

import (
	"context"
	"fmt"
	"os"

	"danny.vn/trachuan/apps/corpus/internal/agentmatch"
)

func main() {
	if err := agentmatch.Run(context.Background(), os.Stdin, os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, "agent matcher failed")
		os.Exit(1)
	}
}
