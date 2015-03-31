import operator
import collections


OPERATORS = {
    operator.and_: 'and',
    operator.or_: 'or',
}


class Compiled(object):
    def __init__(self, expression):
        self.expression = expression
        self.params = self.visit(self.expression)
        
    def visit(self, expr, **kwargs):
        visit_name = None
        if hasattr(expr, '__visit_name__'):
            visit_name = expr.__visit_name__

        if visit_name:
            visit_func = getattr(self, 'visit_{}'.format(visit_name))
            return visit_func(expr, **kwargs)

        if isinstance(expr, dict):
            return self.visit_dict(expr)

        if isinstance(expr, (list, tuple)):
            return self.visit_list(expr)

        return expr

    def visit_params(self, params):
        res = {}
        for k, v in params.items():
            res[self.visit(k)] = self.visit(v)
        return res

    def visit_dict(self, dct):
        return {self.visit(k): self.visit(v) for k, v in dct.items()}

    def visit_list(self, lst):
        return [self.visit(v) for v in lst]


class QueryCompiled(Compiled):
    def visit_literal(self, expr):
        return expr.obj

    def visit_field(self, field):
        return field._name

    def visit_mapping_field(self, field):
        return field._name

    def visit_attributed_field(self, field):
        return field._field._name

    def visit_boost_expression(self, expr):
        return '{}^{}'.format(self.visit(expr.expr), self.visit(expr.weight))

    def visit_query_expression(self, expr):
        return {
            expr.__query_name__: self.visit(expr.params)
        }

    def visit_field_query(self, expr):
        if expr.params:
            params = {expr.__query_key__: self.visit(expr.query)}
            params.update(expr.params)
            return {
                expr.__query_name__: {
                    self.visit(expr.field): params
                }
            }
        else:
            return {
                expr.__query_name__: {
                    self.visit(expr.field): self.visit(expr.query)
                }
            }

    def visit_range(self, expr):
        field_params = {
            self.visit(expr.field): self.visit(expr.params)
        }
        return {
            'range': dict(self.visit(expr.range_params), **field_params)
        }

    def visit_terms(self, expr):
        params = {self.visit(expr.field): self.visit(expr.terms)}
        params.update(self.visit(expr.params))
        return {
            'terms': params
        }

    def visit_multi_match(self, expr):
        params = {
            'query': self.visit(expr.query),
            'fields': [self.visit(f) for f in expr.fields],
        }
        params.update(self.visit(expr.params))
        return {
            'multi_match': params
        }

    def visit_match_all(self, expr):
        return {'match_all': self.visit(expr.params)}

    def visit_query(self, expr):
        params = {
            'query': self.visit(expr.query)
        }
        if expr.params:
            params.update(self.visit(expr.params))
            return {
                'fquery': params
            }
        return params

    def visit_boolean_expression(self, expr):
        if expr.params:
            params = {
                'filters': [self.visit(e) for e in expr.expressions]
            }
            params.update(self.visit(expr.params))
        else:
            params = [self.visit(e) for e in expr.expressions]
        return {
            OPERATORS[expr.operator]: params
        }

    def visit_not(self, expr):
        if expr.params:
            params = {
                'filter': self.visit(expr.expr)
            }
            params.update(self.visit(expr.params))
        else:
            params = self.visit(expr.expr)
        return {
            'not': params
        }

    def visit_sort(self, expr):
        if expr.params:
            params = {'order': self.visit(expr.order)}
            params.update(self.visit(expr.params))
            return {
                self.visit(expr.expr): params
            }
        elif expr.order:
            return {
                self.visit(expr.expr): self.visit(expr.order)
            }
        else:
            return self.visit(expr.expr)

    def visit_agg(self, agg):
        return {
            agg.__agg_name__: self.visit(agg.params)
        }

    def visit_bucket_agg(self, agg):
        params = {
            agg.__agg_name__: self.visit(agg.params)
        }
        if agg._aggregations:
            params['aggregations'] = self.visit(agg._aggregations)
        return params

    def visit_filter_agg(self, agg):
        params = self.visit_bucket_agg(agg)
        params[agg.__agg_name__] = self.visit(agg.filter)
        return params

    def visit_source(self, expr):
        if expr.include or expr.exclude:
            params = {}
            if expr.include:
                params['include'] = self.visit(expr.include)
            if expr.exclude:
                params['exclude'] = self.visit(expr.exclude)
            return params
        if expr.fields is False:
            return False
        return [self.visit(f) for f in expr.fields]

    def visit_rescore(self, rescore):
        query_params = {
            'rescore_query': self.visit(rescore.query)
        }
        if rescore.query_weight is not None:
            query_params['query_weight'] = rescore.query_weight
        if rescore.rescore_query_weight is not None:
            query_params['rescore_query_weight'] = rescore.rescore_query_weight
        if rescore.score_mode is not None:
            query_params['score_mode'] = rescore.score_mode
        params = {'query': query_params}
        if rescore.window_size is not None:
            params['window_size'] = rescore.window_size
        return params

    def visit_search_query(self, query):
        params = {}
        q = query.get_filtered_query()
        if q is not None:
            params['query'] = self.visit(q)
        if query._order_by:
            params['sort'] = [self.visit(o) for o in query._order_by]
        if query._source:
            params['_source'] = self.visit(query._source)
        if query._aggregations:
            params['aggregations'] = self.visit(query._aggregations)
        if query._limit is not None:
            params['size'] = query._limit
        if query._offset is not None:
            params['from'] = query._offset
        if query._rescores:
            params['rescore'] = [self.visit(r) for r in query._rescores]
        if query._post_filters:
            params['post_filter'] = self.visit(query.get_post_filter())
        return params


class MappingCompiled(Compiled):
    def visit_field(self, field):
        field_type = field.get_type()
        mapping = {
            'type': field_type.__visit_name__
        }

        if field_type.doc_cls:
            mapping['properties'] = self.visit(field_type.doc_cls.user_fields)

        if field._fields:
            if isinstance(field._fields, collections.Mapping):
                for fname, ftype in field._fields.items():
                    mapping.setdefault('fields', {}).update({fname: {'type': ftype.__visit_name__}})
            else:
                for f in field._fields:
                    mapping.setdefault('fields', {}).update(f.to_mapping())

        mapping.update(field._mapping_options)
                
        return {
            field.get_name(): mapping
        }

    def visit_mapping_field(self, field):
        if field._mapping_options:
            return {
                field.get_name(): field._mapping_options
            }
        return {}

    def visit_attributed_field(self, field):
        return self.visit(field.get_field())

    def visit_ordered_attributes(self, attrs):
        mapping = {}
        for f in attrs:
            mapping.update(self.visit(f))
        return mapping
        
    def visit_document(self, doc_cls):
        mapping = {}
        mapping.update(doc_cls.__mapping_options__)
        mapping.update(self.visit(doc_cls.mapping_fields))
        mapping['properties'] = self.visit(doc_cls.user_fields)
        for f in doc_cls.dynamic_fields:
            mapping.setdefault('dynamic_templates', []).append(
                {
                    f._field._name: {
                        'path_match': f._field._name,
                        'mapping': next(iter(self.visit(f).values()))
                    }
                }
            )
        return {
            doc_cls.__doc_type__: mapping
        }
    