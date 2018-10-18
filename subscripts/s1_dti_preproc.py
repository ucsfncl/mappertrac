#!/usr/bin/env python3
from subscripts.config import executor_labels
from subscripts.utilities import run,is_integer,write
from os.path import join
from parsl.app.app import python_app

@python_app(executors=executor_labels)
def s1_1_split_timeslices(input_dir, sdir, stdout, container):
    from subscripts.utilities import run,write_start
    from os.path import join
    from shutil import copyfile
    write_start(stdout, "s1_dti_preproc")
    copyfile(join(input_dir,"bvecs"),join(sdir,"bvecs"))
    copyfile(join(input_dir,"bvals"),join(sdir,"bvals"))
    copyfile(join(input_dir,"anat.nii.gz"),join(sdir,"T1.nii.gz"))
    input_data = join(input_dir, "hardi.nii.gz")
    output_prefix = join(sdir,"data_eddy")
    output_data = join(sdir,"data_eddy.nii.gz")
    run("fslroi {} {}_ref 0 1".format(input_data, output_prefix), stdout, container)
    run("fslsplit {} {}_tmp".format(input_data, output_prefix), stdout, container)

@python_app(executors=executor_labels)
def s1_2_timeslice_process(sdir, timeslice, stdout, container, inputs=[]):
    from subscripts.utilities import run
    from os.path import join,exists
    slice_data = join(sdir,"data_eddy_tmp{:04d}.nii.gz".format(timeslice))
    if not exists(slice_data):
        return
    output_prefix = join(sdir,"data_eddy")
    run("flirt -in {0} -ref {1}_ref -nosearch -interp trilinear -o {0} -paddingsize 1 >> {1}.ecclog".format(slice_data, output_prefix), stdout, container)

@python_app(executors=executor_labels)
def s1_3_dti_fit(input_dir, sdir, stdout, container, checksum, inputs=[]):
    from subscripts.utilities import run,smart_remove,write_finish,write_checkpoint
    from os.path import join,exists
    from shutil import copyfile
    from glob import glob
    output_prefix = join(sdir,"data_eddy")
    output_data = join(sdir,"data_eddy.nii.gz")
    timeslices = glob("{}_tmp????.*".format(output_prefix))
    bet = join(sdir,"data_bet.nii.gz")
    bvecs = join(sdir,"bvecs")
    bvals = join(sdir,"bvals")
    bet_mask = join(sdir,"data_bet_mask.nii.gz")
    dti_params = join(sdir,"DTIparams")
    dti_L1 = dti_params + "_L1.nii.gz"
    dti_L2 = dti_params + "_L2.nii.gz"
    dti_L3 = dti_params + "_L3.nii.gz"
    dti_MD = dti_params + "_MD.nii.gz"
    dti_RD = dti_params + "_RD.nii.gz"
    dti_MD = dti_params + "_MD.nii.gz"
    dti_AD = dti_params + "_AD.nii.gz"
    dti_FA = dti_params + "_FA.nii.gz"
    FA = join(sdir,"FA.nii.gz")

    run("fslmerge -t {} {}".format(output_data, " ".join(timeslices)), stdout, container)
    for i in timeslices:
        smart_remove(i)
    for j in glob("{}_ref*".format(output_prefix)):
        smart_remove(j)
    run("bet {} {} -m -f 0.3".format(output_data,bet), stdout, container)
    run("dtifit --verbose -k {} -o {} -m {} -r {} -b {}".format(output_data,dti_params,bet_mask,bvecs,bvals), stdout, container)
    run("fslmaths {} -add {} -add {} -div 3 {}".format(dti_L1,dti_L2,dti_L3,dti_MD), stdout, container)
    run("fslmaths {} -add {} -div 2 {}".format(dti_L2,dti_L3,dti_RD), stdout, container)
    copyfile(dti_L1,dti_AD)
    copyfile(dti_FA,FA)

    write_finish(stdout, "s1_dti_preproc")
    write_checkpoint(sdir, "s1", checksum)

def create_job(input_dir, sdir, stdout, container, checksum):
    input_data = join(input_dir, "hardi.nii.gz")
    timeslices = run("fslinfo {} | sed -n -e '/^dim4/p'".format(input_data), stdout, container).split()
    if not timeslices or not is_integer(timeslices[-1]):
        write(stdout, "Failed to read timeslices from {}".format(input_data))
        return
    num_timeslices = timeslices[-1]
    s1_1_future = s1_1_split_timeslices(input_dir, sdir, stdout, container)
    s1_2_futures = []
    for i in range(int(num_timeslices)):
        s1_2_future = s1_2_timeslice_process(sdir, i, stdout, container, inputs=[s1_1_future])
        s1_2_futures.append(s1_2_future)
    return s1_3_dti_fit(input_dir, sdir, stdout, container, checksum, inputs=s1_2_futures)