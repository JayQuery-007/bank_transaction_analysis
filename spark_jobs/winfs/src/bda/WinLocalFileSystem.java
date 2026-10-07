package bda;

import org.apache.hadoop.fs.LocalFileSystem;

// Spark expects the "file" scheme to be a LocalFileSystem (checksummed wrapper).
public class WinLocalFileSystem extends LocalFileSystem {
    public WinLocalFileSystem() {
        super(new WinRawLocalFileSystem());
    }
}
