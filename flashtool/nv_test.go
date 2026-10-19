package main

import (
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

type fakeNV struct {
	fail             string
	backed, restored []string
}

func (d *fakeNV) OEMDump(part, path string) error {
	d.backed = append(d.backed, part)
	if part == d.fail {
		return errors.New("USB disconnected")
	}
	return os.WriteFile(path, []byte("COMPLETE NV "+part), 0600)
}
func (d *fakeNV) FlashFile(part, path string, progress func(int, int)) error {
	d.restored = append(d.restored, part)
	if part == d.fail {
		return errors.New("USB disconnected")
	}
	return nil
}

var nvParts = []string{"fsc", "fsg", "modemst1", "modemst2"}

func TestBackupNVFailureStopsAtFirstIncompletePartition(t *testing.T) {
	for n, part := range nvParts {
		device := &fakeNV{fail: part}
		if err := backupNV(device, nvParts, t.TempDir()); err == nil {
			t.Fatal("backup error ignored")
		}
		if !reflect.DeepEqual(device.backed, nvParts[:n+1]) {
			t.Fatal(device.backed)
		}
		if len(device.restored) != 0 {
			t.Fatal("restore attempted")
		}
	}
}
func TestRestoreNVPrechecksEntireSetAndStopsOnWriteFailure(t *testing.T) {
	dir := t.TempDir()
	device := &fakeNV{}
	if err := backupNV(device, nvParts, dir); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(filepath.Join(dir, "modemst2.bin")); err != nil {
		t.Fatal(err)
	}
	if err := restoreNV(device, nvParts, dir); err == nil {
		t.Fatal("missing NV accepted")
	}
	if len(device.restored) != 0 {
		t.Fatal("partial set restored")
	}
	if err := os.WriteFile(filepath.Join(dir, "modemst2.bin"), []byte("COMPLETE"), 0600); err != nil {
		t.Fatal(err)
	}
	device.fail = "fsg"
	if err := restoreNV(device, nvParts, dir); err == nil {
		t.Fatal("restore error ignored")
	}
	if !reflect.DeepEqual(device.restored, []string{"fsc", "fsg"}) {
		t.Fatal(device.restored)
	}
}
