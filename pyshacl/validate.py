# -*- coding: utf-8 -*-
from sys import stderr
import logging
import rdflib
import owlrl

if owlrl.json_ld_available:
    import rdflib_jsonld
from pyshacl.errors import ReportableRuntimeError, ValidationFailure
from pyshacl.inference import CustomRDFSSemantics, CustomRDFSOWLRLSemantics
from pyshacl.shacl_graph import SHACLGraph
from pyshacl.consts import RDF_type, SH_conforms, \
    SH_result, SH_ValidationReport, RDFS_Resource, SH_resultMessage, \
    SH_sourceShape, SH_sourceConstraint, SH_resultPath
from pyshacl.rdfutil import load_from_source, clone_graph, \
    clone_node, compare_blank_node, mix_graphs
from pyshacl.monkey import apply_patches

log_handler = logging.StreamHandler(stderr)
log = logging.getLogger(__name__)
for h in log.handlers:
    log.removeHandler(h)  # pragma:no cover
log.addHandler(log_handler)
log.setLevel(logging.INFO)
log_handler.setLevel(logging.INFO)


class Validator(object):
    @classmethod
    def _load_default_options(cls, options_dict):
        options_dict.setdefault('inference', 'none')
        options_dict.setdefault('abort_on_error', False)
        if 'logger' not in options_dict:
            options_dict['logger'] = logging.getLogger(__name__)

    def _run_pre_inference(self, target_graph, inference_option):
        try:
            if inference_option == 'rdfs':
                inferencer = owlrl.DeductiveClosure(CustomRDFSSemantics)
            elif inference_option == 'owlrl':
                inferencer = owlrl.DeductiveClosure(owlrl.OWLRL_Semantics)
            elif inference_option == 'both' or inference_option == 'all'\
                    or inference_option == 'rdfsowlrl':
                inferencer = owlrl.DeductiveClosure(CustomRDFSOWLRLSemantics)
            else:
                raise ReportableRuntimeError(
                    "Don't know how to do '{}' type inferencing."
                    .format(inference_option))
        except Exception as e:  # pragma: no cover
            self.logger.error("Error during creation of OWL-RL Deductive Closure")
            if isinstance(e, ReportableRuntimeError):
                raise e
            raise ReportableRuntimeError("Error during creation of OWL-RL Deductive Closure\n"
                                         "{}".format(str(e.args[0])))
        try:
            inferencer.expand(target_graph)
        except Exception as e:  # pragma: no cover
            self.logger.error("Error while running OWL-RL Deductive Closure")
            raise ReportableRuntimeError("Error while running OWL-RL Deductive Closure\n"
                                         "{}".format(str(e.args[0])))

    @classmethod
    def create_validation_report(cls, conforms, target_graph, shacl_graph, results):
        v_text = "Validation Report\nConforms: {}\n".format(str(conforms))
        result_len = len(results)
        if not conforms:
            assert result_len > 0, \
                "A Non-Conformant Validation Report must have at least one result."
        if result_len > 0:
            v_text += "Results ({}):\n".format(str(result_len))
        vg = rdflib.Graph()
        sg = shacl_graph.graph
        for p, n in sg.namespace_manager.namespaces():
            vg.namespace_manager.bind(p, n)
        vr = rdflib.BNode()
        vg.add((vr, RDF_type, SH_ValidationReport))
        vg.add((vr, SH_conforms, rdflib.Literal(conforms)))
        for result in iter(results):
            _d, _bn, _tr = result
            v_text += _d
            vg.add((vr, SH_result, _bn))
            for tr in iter(_tr):
                s, p, o = tr
                if isinstance(o, tuple):
                    source = o[0]
                    node = o[1]
                    if source == "S":
                        o = clone_node(sg, node, vg)
                    elif source == "D":
                        o = clone_node(target_graph, node, vg)
                    else:  # pragma: no cover
                        raise RuntimeError("Adding node to validation report must have source of either 'D' or 'S'.")
                vg.add((s, p, o))
        return vg, v_text

    def __init__(self, target_graph, *args, shacl_graph=None,
                 ont_graph=None, options=None, **kwargs):
        options = options or {}
        self._load_default_options(options)
        self.options = options
        self.logger = options['logger']
        self.pre_inferenced = kwargs.pop('pre_inferenced', False)
        assert isinstance(target_graph, rdflib.Graph),\
            "target_graph must be a rdflib Graph object"
        self.target_graph = target_graph
        self.ont_graph = ont_graph
        if shacl_graph is None:
            shacl_graph = clone_graph(target_graph, identifier='shacl')
        assert isinstance(shacl_graph, rdflib.Graph),\
            "shacl_graph must be a rdflib Graph object"
        self.shacl_graph = SHACLGraph(shacl_graph, self.logger)

    def run(self):
        if self.ont_graph is not None:
            the_target_graph = mix_graphs(self.target_graph, self.ont_graph)
        else:
            the_target_graph = self.target_graph
        inference_option = self.options.get('inference', 'none')
        if inference_option and not self.pre_inferenced and \
                str(inference_option) != "none":
            self._run_pre_inference(the_target_graph, inference_option)
            self.pre_inferenced = True
        reports = []
        non_conformant = False
        for s in self.shacl_graph.shapes:
            _is_conform, _reports = s.validate(the_target_graph)
            non_conformant = non_conformant or (not _is_conform)
            reports.extend(_reports)
        v_report, v_text = self.create_validation_report((not non_conformant), the_target_graph, self.shacl_graph, reports)
        return (not non_conformant), v_report, v_text


def meta_validate(shacl_graph, inference='rdfs', **kwargs):
    shacl_shacl_graph = meta_validate.shacl_shacl_graph
    if shacl_shacl_graph is None:
        from os import path
        import pickle
        here_dir = path.dirname(__file__)
        pickle_file = path.join(here_dir, "shacl-shacl.pickle")
        with open(pickle_file, 'rb') as shacl_pickle:
            u = pickle.Unpickler(shacl_pickle, fix_imports=False)
            shacl_shacl_store = u.load()
        shacl_shacl_graph = rdflib.Graph(store=shacl_shacl_store, identifier="http://www.w3.org/ns/shacl-shacl")
        meta_validate.shacl_shacl_graph = shacl_shacl_graph
    shacl_graph = load_from_source(shacl_graph,
                                  rdf_format=kwargs.pop('shacl_graph_format', None))
    _ = kwargs.pop('meta_shacl', None)
    return validate(shacl_graph, shacl_graph=shacl_shacl_graph, inference=inference, **kwargs)
meta_validate.shacl_shacl_graph = None


def validate(data_graph, *args, shacl_graph=None, ont_graph=None, inference=None, abort_on_error=False, **kwargs):
    """
    :param data_graph: rdflib.Graph or file path or web url of the data to validate
    :type data_graph: rdflib.Graph | str
    :param args:
    :type args: list
    :param shacl_graph: rdflib.Graph or file path or web url of the SHACL Shapes graph to use to validate the data graph
    :type shacl_graph: rdflib.Graph | str
    :param ont_graph: rdflib.Graph or file path or web url of an extra ontology document to mix into the data graph
    :type ont_graph: rdflib.Graph | str
    :param inference:
    :type inference: str | None
    :param abort_on_error:
    :type abort_on_error: bool | None
    :param kwargs:
    :return:
    """

    if kwargs.get('debug', False):
        log_handler.setLevel(logging.DEBUG)
        log.setLevel(logging.DEBUG)
    apply_patches()
    depr_target_graph = kwargs.pop('target_graph', None)
    if depr_target_graph:
        # TODO:coverage: will be fixed when we remove this deprecation
        msg = "The target_graph parameter is deprecated. Use data_graph instead."
        log.warning(msg)
        if data_graph is None:
            data_graph = depr_target_graph
    depr_target_graph_format = kwargs.pop('target_graph_format', None)
    if depr_target_graph_format:
        # TODO:coverage: will be fixed when we remove this deprecation
        msg = "The target_graph_format parameter is deprecated. Use data_graph_format instead."
        log.warning(msg)
    do_check_dash_result = kwargs.pop('check_dash_result', False)
    do_check_sht_result = kwargs.pop('check_sht_result', False)
    if kwargs.get('meta_shacl', False):
        to_meta_val = shacl_graph or data_graph
        conforms, v_r, v_t = meta_validate(to_meta_val, inference=inference, **kwargs)
        if not conforms:
            msg = "Shacl File does not validate against the Shacl Shapes Shacl file.\n{}"\
                  .format(v_t)
            log.error(msg)
            raise ReportableRuntimeError(msg)
    do_owl_imports = kwargs.pop('do_owl_imports', False)
    data_graph_format = kwargs.pop('data_graph_format', None)
    if data_graph_format is None:
        # TODO:coverage: will be fixed when we remove this deprecation
        data_graph_format = depr_target_graph_format
    data_graph = load_from_source(data_graph,
                                  rdf_format=data_graph_format,
                                  do_owl_imports=False)  # no imports on data_graph
    ont_graph_format = kwargs.pop('ont_graph_format', None)
    if ont_graph is not None:
        ont_graph = load_from_source(ont_graph,
                                     rdf_format=ont_graph_format,
                                     do_owl_imports=do_owl_imports)
    shacl_graph_format = kwargs.pop('shacl_graph_format', None)
    if shacl_graph is not None:
        shacl_graph = load_from_source(shacl_graph,
                                       rdf_format=shacl_graph_format,
                                       do_owl_imports=do_owl_imports)
    try:
        validator = Validator(
            data_graph, shacl_graph=shacl_graph, ont_graph=ont_graph,
            options={'inference': inference, 'abort_on_error': abort_on_error,
                     'logger': log})
        conforms, report_graph, report_text = validator.run()
    except ValidationFailure as e:
        conforms = False
        report_graph = e
        report_text = "Validation Failure - {}".format(e.message)
    if do_check_dash_result:
        passes = check_dash_result(report_graph, shacl_graph or data_graph)
        return passes, report_graph, report_text
    if do_check_sht_result:
        (sht_graph, sht_result_node) = kwargs.pop('sht_validate', (False, None))
        passes = check_sht_result(report_graph, sht_graph or shacl_graph or data_graph, sht_result_node)
        return passes, report_graph, report_text
    do_serialize_report_graph = kwargs.pop('serialize_report_graph', False)
    if do_serialize_report_graph and isinstance(report_graph, rdflib.Graph):
        if not (isinstance(do_serialize_report_graph, str)):
            do_serialize_report_graph = 'turtle'
        report_graph = report_graph.serialize(None, encoding='utf-8',
                                              format=do_serialize_report_graph)
    return conforms, report_graph, report_text


def clean_validation_reports(actual_graph, actual_report, expected_graph, expected_report):
    # remove rdfs-added stuff
    # remove resultMessage if expected_report does not include result_message
    # expected_graph.remove((expected_report, RDF_type, RDFS_Resource))
    # actual_graph.remove((actual_report, RDF_type, RDFS_Resource))
    expected_graph.remove((None, RDF_type, RDFS_Resource))
    actual_graph.remove((None, RDF_type, RDFS_Resource))
    expected_results = list(expected_graph.objects(expected_report, SH_result))
    actual_results = list(actual_graph.objects(actual_report, SH_result))
    er_has_messages = None
    for er in expected_results:
        expected_graph.remove((er, RDF_type, RDFS_Resource))
        er_has_messages = list(expected_graph.objects(er, SH_resultMessage))
        # sourceShapes = list(expected_graph.objects(er, SH_sourceShape))
        # for s in sourceShapes:
        #     expected_graph.remove((s, RDF_type, RDFS_Resource))
        # resultPaths = list(expected_graph.objects(er, SH_resultPath))
        # for r in resultPaths:
        #     expected_graph.remove((r, RDF_type, RDFS_Resource))
        # sourceConstraints = list(expected_graph.objects(er, SH_sourceConstraint))
        # for s in sourceConstraints:
        #     expected_graph.remove((s, RDF_type, RDFS_Resource))
    if er_has_messages and len(er_has_messages) > 0:
        # keep messages in actual
        pass
    else:
        for ar in actual_results:
            actual_graph.remove((ar, SH_resultMessage, None))
    return True


def compare_validation_reports(report_graph, expected_graph, expected_result):
    expected_conforms = expected_graph.objects(expected_result, SH_conforms)
    expected_conforms = set(expected_conforms)
    if len(expected_conforms) < 1:  # pragma: no cover
        raise ReportableRuntimeError("Cannot check the expected result, the given expectedResult does not have an sh:conforms.")
    expected_conforms = next(iter(expected_conforms))
    expected_result_nodes = expected_graph.objects(expected_result, SH_result)
    expected_result_nodes = set(expected_result_nodes)
    expected_result_node_count = len(expected_result_nodes)

    validation_reports = report_graph.subjects(RDF_type, SH_ValidationReport)
    validation_reports = set(validation_reports)
    if len(validation_reports) < 1:  # pragma: no cover
        raise ReportableRuntimeError("Cannot check the validation report, the report graph does not contain a ValidationReport")
    validation_report = next(iter(validation_reports))
    clean_validation_reports(
        report_graph, validation_report, expected_graph, expected_result)
    eq = compare_blank_node(report_graph, validation_report, expected_graph, expected_result)
    if eq != 0:
        return False
    report_conforms = report_graph.objects(validation_report, SH_conforms)
    report_conforms = set(report_conforms)
    if len(report_conforms) < 1:  # pragma: no cover
        raise ReportableRuntimeError("Cannot check the validation report, the report graph does not have an sh:conforms.")
    report_conforms = next(iter(report_conforms))

    if bool(expected_conforms.value) != bool(report_conforms.value):
        # TODO:coverage: write a test for this
        log.error("Expected Result Conforms value is different from Validation Report's Conforms value.")
        return False

    report_result_nodes = report_graph.objects(validation_report, SH_result)
    report_result_nodes = set(report_result_nodes)
    report_result_node_count = len(report_result_nodes)

    if expected_result_node_count != report_result_node_count:
        # TODO:coverage: write a test for this
        log.error("Number of expected result's sh:result entries is different from Validation Report's sh:result entries.\n"
                  "Expected {}, got {}.".format(expected_result_node_count, report_result_node_count))
        return False
    return True

def check_dash_result(report_graph, expected_result_graph):
    DASH = rdflib.namespace.Namespace('http://datashapes.org/dash#')
    DASH_TestCase = DASH.term('GraphValidationTestCase')
    DASH_expectedResult = DASH.term('expectedResult')

    test_cases = expected_result_graph.subjects(RDF_type, DASH_TestCase)
    test_cases = set(test_cases)
    if len(test_cases) < 1:  # pragma: no cover
        raise ReportableRuntimeError("Cannot check the expected result, the given expected result graph does not have a GraphValidationTestCase.")
    test_case = next(iter(test_cases))
    expected_results = expected_result_graph.objects(test_case, DASH_expectedResult)
    expected_results = set(expected_results)
    if len(expected_results) < 1: # pragma: no cover
        raise ReportableRuntimeError("Cannot check the expected result, the given GraphValidationTestCase does not have an expectedResult.")
    expected_result = next(iter(expected_results))
    return compare_validation_reports(report_graph, expected_result_graph, expected_result)

def check_sht_result(report_graph, sht_graph, sht_result_node):
    SHT = rdflib.namespace.Namespace('http://www.w3.org/ns/shacl-test#')
    types = set(sht_graph.objects(sht_result_node, RDF_type))
    expected_failure = (sht_result_node == SHT.Failure)
    if expected_failure and isinstance(report_graph, ValidationFailure):
        return True
    elif isinstance(report_graph, ValidationFailure):
        # TODO:coverage: write a test for this
        log.error("Validation Report indicates a Validation Failure, but the SHT entry does not expect a failure.")
        return False
    elif expected_failure:
        # TODO:coverage: write a test for this
        log.error("SHT expects a Validation Failure, but the Validation Report does not indicate a Validation Failure.")
        return False
    if SH_ValidationReport not in types:
        raise ReportableRuntimeError(
            "SHT expected result must have type sh:ValidationReport")
    return compare_validation_reports(report_graph, sht_graph, sht_result_node)





