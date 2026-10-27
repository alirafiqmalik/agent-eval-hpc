use vstd::contrib::exec_spec::*;
use vstd::prelude::*;

verus! {
    exec_spec_unverified! {
        pub struct In1 {
            pub s: Seq<char>,
        }

        pub struct Out {
            pub answers: Seq<i64>,
        }

        pub open spec fn pre_spec(in1: In1) -> bool {

}

        pub open spec fn post_spec(in1: In1, out: Out) -> bool {

}

    }

pub open proof fn pre_spec_soundness_proof(in1: In1) -> bool {

}

pub open proof fn pre_spec_completeness_proof(in1: In1) -> bool {

}

pub open proof fn post_spec_soundness_proof(in1: In1, out: Out) -> bool {

}

pub open proof fn post_spec_completeness_proof(in1: In1, out: Out) -> bool {

}

fn check_pre_spec_completeness() {
        // __PASTE_out.input_defn__

        proof {
            pre_spec_completeness_proof(exec_in1.deep_view());
        }

        assert(pre_spec(exec_in1.deep_view()));

    }

fn check_pre_spec_soundness() {
        // __PASTE_out.input_defn__

        proof {
            pre_spec_soundness_proof(exec_in1.deep_view());
        }

        assert(!pre_spec(exec_in1.deep_view()));

    }

fn check_post_spec_completeness() {
        // __PASTE_out.input_defn__
        // __PASTE_out.gt_output_defn__

        proof {
            post_spec_completeness_proof(exec_in1.deep_view(), exec_out.deep_view());
        }

        assert(post_spec(exec_in1.deep_view(), exec_out.deep_view()));

    }

fn check_post_spec_soundness() {
        // __PASTE_out.input_defn__
        // __PASTE_out.gt_output_defn__

        proof {
            post_spec_soundness_proof(exec_in1.deep_view(), exec_out.deep_view());
        }

        assert(!post_spec(exec_in1.deep_view(), exec_out.deep_view()));

    }

pub fn main_exec_pre_spec_check() {
        // __PASTE_out.input_defn__
        let result = exec_pre_spec(&exec_in1);
        // The evaluator may rewrite this assertion polarity for soundness cases.
        assert!(result);
    }

pub fn main_exec_post_spec_check() {
        // __PASTE_out.input_defn__
        // __PASTE_out.gt_output_defn__
        let result = exec_post_spec(&exec_in1, &exec_out);
        // The evaluator may rewrite this assertion polarity for soundness cases.
        assert!(result);
    }

fn main() {
        unimplemented!("main() body is replaced by evaluator with a direct function call");
    }

}