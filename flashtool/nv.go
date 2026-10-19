package main

import (
	"fmt"
	"os"
	"path/filepath"
)

type nvDevice interface {
	OEMDump(string, string) error
	FlashFile(string, string, func(int, int)) error
}

func backupNV(device nvDevice, partitions []string, dir string) error {
	for _, part := range partitions {
		if err := device.OEMDump(part, filepath.Join(dir, part+".bin")); err != nil {
			return fmt.Errorf("NV backup %s failed; flashing stopped: %w", part, err)
		}
	}
	return nil
}

func restoreNV(device nvDevice, partitions []string, dir string) error {
	// Check the entire set before restoring its first member.
	for _, part := range partitions {
		info, err := os.Stat(filepath.Join(dir, part+".bin"))
		if err != nil {
			return fmt.Errorf("required NV backup %s: %w", part, err)
		}
		if !info.Mode().IsRegular() || info.Size() == 0 {
			return fmt.Errorf("NV backup %s is empty/not a file", part)
		}
	}
	for _, part := range partitions {
		if err := device.FlashFile(part, filepath.Join(dir, part+".bin"), nil); err != nil {
			return fmt.Errorf("NV restore %s failed; flashing stopped: %w", part, err)
		}
	}
	return nil
}
