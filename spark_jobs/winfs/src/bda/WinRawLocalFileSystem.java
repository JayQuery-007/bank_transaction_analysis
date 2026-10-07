package bda;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

import org.apache.hadoop.fs.FileStatus;
import org.apache.hadoop.fs.Path;
import org.apache.hadoop.fs.RawLocalFileSystem;
import org.apache.hadoop.fs.permission.FsPermission;

/**
 * Local filesystem for running Spark on Windows without winutils.exe / hadoop.dll.
 * Hadoop's default local FS calls those native helpers to chmod files and check
 * read access. Neither matters on a single-user dev machine, so this uses plain
 * java.io.File instead. Wrapped by WinLocalFileSystem; only enabled on Windows (see spark_jobs/common.py).
 */
public class WinRawLocalFileSystem extends RawLocalFileSystem {

    @Override
    public void setPermission(Path p, FsPermission permission) {
    }

    @Override
    public FileStatus getFileStatus(Path f) throws IOException {
        File local = pathToFile(f);
        if (!local.exists()) {
            throw new FileNotFoundException("File " + f + " does not exist");
        }
        boolean dir = local.isDirectory();
        return new FileStatus(dir ? 0 : local.length(), dir, 1, getDefaultBlockSize(f),
                local.lastModified(), makeQualified(f));
    }

    @Override
    public FileStatus[] listStatus(Path f) throws IOException {
        File local = pathToFile(f);
        if (!local.exists()) {
            throw new FileNotFoundException("File " + f + " does not exist");
        }
        if (!local.isDirectory()) {
            return new FileStatus[] {getFileStatus(f)};
        }
        String[] names = local.list();
        if (names == null) {
            throw new IOException("Could not list " + f);
        }
        List<FileStatus> result = new ArrayList<>();
        for (String name : names) {
            try {
                result.add(getFileStatus(new Path(f, name)));
            } catch (FileNotFoundException ignored) {
                // deleted between list() and stat, e.g. by a concurrent task commit
            }
        }
        return result.toArray(new FileStatus[0]);
    }
}
