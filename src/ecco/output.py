import os
import json
import ecco
from IPython import display as d
from ecco import util, lm_plots
import random
import matplotlib.pyplot as plt
import numpy as np
import torch
from torch.nn import functional as F
from sklearn import decomposition
from typing import Optional, List


class OutputSeq:
    """An OutputSeq object is the result of running a language model on some input data. It contains not only the output
    sequence of words generated by the model, but also other data collecting during the generation process
    that is useful to analyze the model.

    In addition to the data, the object has methods to create plots
    and visualizations of that collected data. These include:

    - [layer_predictions()](./#ecco.output.OutputSeq.layer_predictions) <br/>
    Which tokens did the model consider as the best outputs for a specific position in the sequence?
    - [rankings()](./#ecco.output.OutputSeq.rankings) <br/>
    After the model chooses an output token for a specific position, this visual looks back at the ranking
    of this token at each layer of the model when it was generated (layers assign scores to candidate output tokens,
    the higher the "probability" score, the higher the ranking of the token).
    - [rankings_watch()](./#ecco.output.OutputSeq.rankings_watch) <br />
    Shows the rankings of multiple tokens as the model scored them for a single position. For example, if the input is
    "The cat \_\_\_", we use this method to observe how the model ranked the words "is", "are", "was" as candidates
    to fill in the blank.
    - [saliency()](./#ecco.output.OutputSeq.saliency) <br />
    How important was each input token in the selection of calculating the output token?


    To process neuron activations, OutputSeq has methods to reduce the dimensionality and reveal underlying patterns in
    neuron firings. These are:

    - [run_nmf()](./#ecco.output.OutputSeq.run_nmf)


    """
    def __init__(self,
                 token_ids=None,
                 n_input_tokens=None,
                 tokenizer=None,
                 output_text=None,
                 tokens=None,
                 hidden_states=None,
                 attribution=None,
                 activations=None,
                 collect_activations_layer_nums=None,
                 attention=None,
                 model_outputs=None,
                 lm_head=None,
                 device='cpu'):
        """

        Args:
            token_ids: The input token ids. Dimensions: (batch, position)
            n_input_tokens: Int. The number of input tokens in the sequence.
            tokenizer: huggingface tokenizer associated with the model generating this output
            output_text: The output text generated by the model (if processed with generate())
            tokens: A list of token text. Shorthand to passing the token ids by the tokenizer.
                dimensions are (batch, position)
            hidden_states: A tensor of  dimensions (layer, position, hidden_dimension).
                In layer, index 0 is for embedding hidden_state.
            attribution: A list of attributions. One element per generated token.
                Each element is a list giving a value for tokens from 0 to right before the generated token.
            activations: The activations collected from model processing.
                Shape is (batch, layer, neurons, position)
            collect_activations_layer_nums:
            attention: The attention tensor retrieved from the language model
            model_outputs: Raw return object returned by the model
            lm_head: The trained language model head from a language model projecting a
                hidden state to an output vocabulary associated with teh tokenizer.
            device: "cuda" or "cpu"
        """
        self.token_ids = token_ids
        self.tokenizer = tokenizer
        self.n_input_tokens = n_input_tokens
        self.output_text = output_text
        self.tokens = tokens
        self.hidden_states = hidden_states
        self.attribution = attribution
        self.activations = activations
        self.collect_activations_layer_nums = collect_activations_layer_nums
        self.model_outputs = model_outputs
        self.attention_values = attention
        self.lm_head = lm_head
        self.device = device
        self._path = os.path.dirname(ecco.__file__)

    def __str__(self):
        return "<LMOutput '{}' # of lm outputs: {}>".format(self.output_text, len(self.hidden_states))

    def to(self, tensor: torch.Tensor):
        if self.device == 'cuda':
            return tensor.to('cuda')
        return tensor

    def explorable(self, printJson: Optional[bool] = False):

        tokens = []
        for idx, token in enumerate(self.tokens[0]):
            type = "input" if idx < self.n_input_tokens else 'output'

            tokens.append({'token': token,
                           'token_id': int(self.token_ids[0][idx]),
                           'type': type
                           })

        data = {
            'tokens': tokens
        }

        d.display(d.HTML(filename=os.path.join(self._path, "html", "setup.html")))
        d.display(d.HTML(filename=os.path.join(self._path, "html", "basic.html")))
        viz_id = 'viz_{}'.format(round(random.random() * 1000000))
        js = """
         requirejs(['basic', 'ecco'], function(basic, ecco){{
            const viz_id = basic.init()

            ecco.renderOutputSequence(viz_id, {})
         }}, function (err) {{
            console.log(err);
        }})""".format(data)
        d.display(d.Javascript(js))

        if printJson:
            print(data)

    def __call__(self, position=None, **kwargs):

        if position is not None:
            self.position(position, **kwargs)

        else:
            self.saliency(**kwargs)

    def position(self, position, attr_method='grad_x_input'):

        if (position < self.n_input_tokens) or (position > len(self.tokens) - 1):
            raise ValueError("'position' should indicate a position of a generated token. "
                             "Accepted values for this sequence are between {} and {}."
                             .format(self.n_input_tokens, len(self.tokens) - 1))

        importance_id = position - self.n_input_tokens
        tokens = []
        attribution = self.attribution[attr_method]
        for idx, token in enumerate(self.tokens):
            type = "input" if idx < self.n_input_tokens else 'output'
            if idx < len(attribution[importance_id]):
                imp = attribution[importance_id][idx]
            else:
                imp = -1

            tokens.append({'token': token,
                           'token_id': int(self.token_ids[idx]),
                           'type': type,
                           'value': str(imp)  # because json complains of floats
                           })

        data = {
            'tokens': tokens
        }

        d.display(d.HTML(filename=os.path.join(self._path, "html", "setup.html")))
        d.display(d.HTML(filename=os.path.join(self._path, "html", "basic.html")))
        viz_id = 'viz_{}'.format(round(random.random() * 1000000))
        js = """
         requirejs(['basic', 'ecco'], function(basic, ecco){{
            const viz_id = basic.init()

            ecco.renderSeqHighlightPosition(viz_id, {}, {})
         }}, function (err) {{
            console.log(err);
        }})""".format(position, data)
        d.display(d.Javascript(js))

    def saliency(self, attr_method: Optional[str] = 'grad_x_input', style="minimal", **kwargs):
        """
Explorable showing saliency of each token generation step.
Hovering-over or tapping an output token imposes a saliency map on other tokens
showing their importance as features to that prediction.

Examples:

```python
import ecco
lm = ecco.from_pretrained('distilgpt2')
text= "The countries of the European Union are:\n1. Austria\n2. Belgium\n3. Bulgaria\n4."
output = lm.generate(text, generate=20, do_sample=True)

# Show saliency explorable
output.saliency()
```

Which creates the following interactive explorable:
![input saliency example 1](../../img/saliency_ex_1.png)

If we want more details on the saliency values, we can use the detailed view:

```python
# Show detailed explorable
output.saliency(style="detailed")
```

Which creates the following interactive explorable:

![input saliency example 2 - detailed](../../img/saliency_ex_2.png)


Details:
This view shows the Gradient * Inputs method of input saliency. The attribution values are calculated across the
embedding dimensions, then we use the L2 norm to calculate a score for each token (from the values of its embeddings dimension)
To get a percentage value, we normalize the scores by dividing by the sum of the attribution scores for all
the tokens in the sequence.
        """
        position = self.n_input_tokens

        importance_id = position - self.n_input_tokens
        tokens = []
        attribution = self.attribution[attr_method]
        for idx, token in enumerate(self.tokens[0]):
            type = "input" if idx < self.n_input_tokens else 'output'
            if idx < len(attribution[importance_id]):
                imp = attribution[importance_id][idx]
            else:
                imp = 0

            tokens.append({'token': token,
                           'token_id': int(self.token_ids[0][idx]),
                           'type': type,
                           'value': str(imp),  # because json complains of floats
                           'position': idx
                           })

        data = {
            'tokens': tokens,
            'attributions': [att.tolist() for att in attribution]
        }

        d.display(d.HTML(filename=os.path.join(self._path, "html", "setup.html")))
        d.display(d.HTML(filename=os.path.join(self._path, "html", "basic.html")))
        # viz_id = 'viz_{}'.format(round(random.random() * 1000000))

        if (style == "minimal"):
            js = f"""
             requirejs(['basic', 'ecco'], function(basic, ecco){{
                const viz_id = basic.init()
                // ecco.interactiveTokens(viz_id, {{}})
                window.ecco[viz_id] = new ecco.MinimalHighlighter({{
                parentDiv: viz_id,
                data: {data},
                preset: 'viridis'
             }})

             window.ecco[viz_id].init();
             window.ecco[viz_id].selectFirstToken();

             }}, function (err) {{
                console.log(err);
            }})"""
        elif (style == "detailed"):

            js = f"""
             requirejs(['basic', 'ecco'], function(basic, ecco){{
                const viz_id = basic.init()
                window.ecco[viz_id] = ecco.interactiveTokens(viz_id, {data})

             }}, function (err) {{
                console.log(err);
            }})"""

        d.display(d.Javascript(js))

        if 'printJson' in kwargs and kwargs['printJson']:
            print(data)
            return data

    def _repr_html_(self, **kwargs):
        # if util.type_of_script() == "jupyter":
        self.explorable(**kwargs)
        return '<OutputSeq>'

    # def plot_feature_importance_barplots(self):
    #     """
    #     Barplot showing the improtance of each input token. Prints one barplot
    #     for each generated token.
    #     :return:
    #     """
    #     printable_tokens = [repr(token) for token in self.tokens]
    #     for i in self.importance:
    #         importance = i.numpy()
    #         lm_plots.token_barplot(printable_tokens, importance)
    #         # print(i.numpy())
    #         plt.show()

    def layer_predictions(self, position: int = 1, topk: Optional[int] = 10, layer: Optional[int] = None, **kwargs):
        """
        Visualization plotting the topk predicted tokens after each layer (using its hidden state).

        Example:
        ![prediction scores](../../img/layer_predictions_ex_london.png)

        Args:
            position: The index of the output token to trace
            topk: Number of tokens to show for each layer
            layer: None shows all layers. Can also pass an int with the layer id to show only that layer
        """

        hidden_states = self.hidden_states

        if position == 0:
            raise ValueError(f"'position' is set to 0. There is never a hidden state associated with this position."
                             f"Possible values are 1 and above -- the position of the token of interest in the sequence")
        # watch = self.to(torch.tensor([self.token_ids[self.n_input_tokens]]))
        # There is one lm output per generated token. To get the index
        output_index = position - self.n_input_tokens
        if layer is not None:
            # If a layer is specified, choose it only.
            hidden_states = hidden_states[layer + 1].unsqueeze(0)
        else:
            # include all layers except the first
            hidden_states = hidden_states[1:]

        k = topk
        top_tokens = []
        probs = []
        data = []

        for layer_no, h in enumerate(hidden_states):
            hidden_state = h[position - 1]
            # Use lm_head to project the layer's hidden state to output vocabulary
            logits = self.lm_head(self.to(hidden_state))
            softmax = F.softmax(logits, dim=-1)
            # softmax dims are (number of words in vocab) - 50257 in GPT2
            sorted_softmax = self.to(torch.argsort(softmax))
            # Not currently used. If we're "watching" a specific token, this gets its ranking
            # idx = sorted_softmax.shape[0] - torch.nonzero((sorted_softmax == watch)).flatten()

            layer_top_tokens = [self.tokenizer.decode(t) for t in sorted_softmax[-k:]][::-1]
            top_tokens.append(layer_top_tokens)
            layer_probs = softmax[sorted_softmax[-k:]].cpu().detach().numpy()[::-1]
            probs.append(layer_probs.tolist())

            # Package in output format
            layer_data = []
            for idx, (token, prob) in enumerate(zip(layer_top_tokens, layer_probs)):
                # print(layer_no, idx, token)
                layer_num = layer if layer is not None else layer_no
                layer_data.append({'token': token,
                                   'prob': str(prob),
                                   'ranking': idx + 1,
                                   'layer': layer_num
                                   })

            data.append(layer_data)

        d.display(d.HTML(filename=os.path.join(self._path, "html", "setup.html")))
        d.display(d.HTML(filename=os.path.join(self._path, "html", "basic.html")))

        js = f"""
         requirejs(['basic', 'ecco'], function(basic, ecco){{
            const viz_id = basic.init()


            let pred = new ecco.LayerPredictions({{
                parentDiv: viz_id,
                data:{json.dumps(data)}
            }})
            pred.init()
         }}, function (err) {{
            console.log(viz_id, err);
        }})"""
        d.display(d.Javascript(js))

        if 'printJson' in kwargs and kwargs['printJson']:
            print(data)
            return data

    def rankings(self, **kwargs):
        """
        Plots the rankings (across layers) of the tokens the model selected.
        Each column is a position in the sequence. Each row is a layer.

        ![Rankings watch](../../img/rankings_ex_eu_1.png)
        """

        hidden_states = self.hidden_states

        n_layers = len(hidden_states)
        position = hidden_states[0].shape[0] - self.n_input_tokens + 1

        predicted_tokens = np.empty((n_layers - 1, position), dtype='U25')
        rankings = np.zeros((n_layers - 1, position), dtype=np.int32)
        token_found_mask = np.ones((n_layers - 1, position))

        # loop through layer levels
        for i, level in enumerate(hidden_states[1:]):
            # Loop through generated/output positions
            for j, hidden_state in enumerate(level[self.n_input_tokens - 1:]):
                # Project hidden state to vocabulary
                # (after debugging pain: ensure input is on GPU, if appropriate)
                logits = self.lm_head(self.to(hidden_state))
                # Sort by score (ascending)
                sorted = torch.argsort(logits)
                # What token was sampled in this position?

                token_id = torch.tensor(self.token_ids[0][self.n_input_tokens + j])
                # token_id = self.token_ids.clone().detach()[self.n_input_tokens + j]
                # What's the index of the sampled token in the sorted list?
                r = torch.nonzero((sorted == token_id)).flatten()
                # subtract to get ranking (where 1 is the top scoring, because sorting was in ascending order)
                ranking = sorted.shape[0] - r
                token = self.tokenizer.decode([token_id])
                predicted_tokens[i, j] = token
                rankings[i, j] = int(ranking)
                if token_id == self.token_ids[0][j + 1]:
                    token_found_mask[i, j] = 0

        input_tokens = [repr(t) for t in self.tokens[0][self.n_input_tokens - 1:-1]]
        output_tokens = [repr(t) for t in self.tokens[0][self.n_input_tokens:]]
        lm_plots.plot_inner_token_rankings(input_tokens,
                                           output_tokens,
                                           rankings,
                                           **kwargs)

        if 'printJson' in kwargs and kwargs['printJson']:
            data = {'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    'rankings': rankings,
                    'predicted_tokens': predicted_tokens}
            print(data)
            return data

    def rankings_watch(self, watch: List[int] = None, position: int = -1, **kwargs):
        """
        Plots the rankings of the tokens whose ids are supplied in the watch list.
        Only considers one position.

        ![Rankings plot](../../img/ranking_watch_ex_is_are_1.png)
        """
        if position != -1:
            position = position - 1  # e.g. position 5 corresponds to activation 4

        hidden_states = self.hidden_states

        n_layers = len(hidden_states)
        n_tokens_to_watch = len(watch)

        rankings = np.zeros((n_layers - 1, n_tokens_to_watch), dtype=np.int32)

        # loop through layer levels
        for i, level in enumerate(hidden_states[1:]):  # Skip the embedding layer
            # Loop through generated/output positions
            for j, token_id in enumerate(watch):
                hidden_state = level[position]
                # Project hidden state to vocabulary
                # (after debugging pain: ensure input is on GPU, if appropriate)
                logits = self.lm_head(self.to(hidden_state))
                # Sort by score (ascending)
                sorted = torch.argsort(logits)
                # What token was sampled in this position?
                token_id = torch.tensor(token_id)
                # What's the index of the sampled token in the sorted list?
                r = torch.nonzero((sorted == token_id)).flatten()
                # subtract to get ranking (where 1 is the top scoring, because sorting was in ascending order)
                ranking = sorted.shape[0] - r
                rankings[i, j] = int(ranking)

        input_tokens = [t for t in self.tokens[0]]
        output_tokens = [repr(self.tokenizer.decode(t)) for t in watch]

        lm_plots.plot_inner_token_rankings_watch(input_tokens,
                                                 output_tokens,
                                                 rankings)

        if 'printJson' in kwargs and kwargs['printJson']:
            data = {'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    'rankings': rankings}
            print(data)
            return data

    def attention(self, attention_values=None, layer=0, **kwargs):

        position = self.n_input_tokens
        # importance_id = position - self.n_input_tokens

        importance_id = self.n_input_tokens - 1  # Sete first values to first output token
        tokens = []
        if attention_values:
            attn = attention_values
        else:

            attn = self.attention_values[layer]
            # normalize attention heads
            attn = attn.sum(axis=1) / attn.shape[1]

        for idx, token in enumerate(self.tokens):
            # print(idx, attn.shape)
            type = "input" if idx < self.n_input_tokens else 'output'
            if idx < len(attn[0][importance_id]):
                attention_value = attn[0][importance_id][idx].cpu().detach().numpy()
            else:
                attention_value = 0

            tokens.append({'token': token,
                           'token_id': int(self.token_ids[idx]),
                           'type': type,
                           'value': str(attention_value),  # because json complains of floats
                           'position': idx
                           })

        data = {
            'tokens': tokens,
            'attributions': [att.tolist() for att in attn[0].cpu().detach().numpy()]
        }

        d.display(d.HTML(filename=os.path.join(self._path, "html", "setup.html")))
        d.display(d.HTML(filename=os.path.join(self._path, "html", "basic.html")))
        viz_id = 'viz_{}'.format(round(random.random() * 1000000))
        js = """
         requirejs(['basic', 'ecco'], function(basic, ecco){{
            const viz_id = basic.init()

            ecco.interactiveTokens(viz_id, {})
         }}, function (err) {{
            console.log(err);
        }})""".format(data)
        d.display(d.Javascript(js))

        if 'printJson' in kwargs and kwargs['printJson']:
            print(data)

    def run_nmf(self, **kwargs):
        """
        Run Non-negative Matrix Factorization on network activations of FFNN. Returns an [NMF]() object which holds
        the factorization model and data and methods to visualize them.


        """
        return NMF(self.activations,
                   n_input_tokens=self.n_input_tokens,
                   token_ids=self.token_ids,
                   _path=self._path,
                   tokens=self.tokens,
                   collect_activations_layer_nums=self.collect_activations_layer_nums,
                   **kwargs)

class NMF:
    """ Conducts NMF and holds the models and components """

    def __init__(self, activations: np.ndarray,
                 n_input_tokens: int = 0,
                 token_ids: torch.Tensor = torch.Tensor(0),
                 _path: str = '',
                 n_components: int = 10,
                 # from_layer: Optional[int] = None,
                 # to_layer: Optional[int] = None,
                 tokens: Optional[List[str]] = None,
                 collect_activations_layer_nums: Optional[List[int]] = None,
                 **kwargs):
        """
        Receives a neuron activations tensor from OutputSeq and decomposes it using NMF into the number
        of components specified by `n_components`. For example, a model like `distilgpt2` has 18,000+
        neurons. Using NMF to reduce them to 32 components can reveal interesting underlying firing
        patterns.

        Args:
            activations: Activations tensor. Dimensions: (batch, layer, neuron, position)
            n_input_tokens: Number of input tokens.
            token_ids: List of tokens ids.
            _path: Disk path to find javascript that create interactive explorables
            n_components: Number of components/factors to reduce the neuron factors to.
            tokens: The text of each token.
            collect_activations_layer_nums: The list of layer ids whose activtions were collected. If
            None, then all layers were collected.
            """

        if activations == []:
            raise ValueError(f"No activation data found. Make sure 'activations=True' was passed to "
                             f"ecco.from_pretrained().")

        self._path = _path
        self.token_ids = token_ids
        self.n_input_tokens = n_input_tokens

        from_layer = kwargs['from_layer'] if 'from_layer' in kwargs else None
        to_layer = kwargs['to_layer'] if 'to_layer' in kwargs else None

        merged_act = self.reshape_activations(activations,
                                              from_layer,
                                              to_layer,
                                              collect_activations_layer_nums)
        # 'merged_act' is now ( neuron (and layer), position (and batch) )

        activations = merged_act

        self.tokens = tokens
        # Run NMF. 'activations' is neuron activations shaped (neurons (and layers), positions (and batches))
        n_output_tokens = activations.shape[-1]
        n_layers = activations.shape[0]
        n_components = min([n_components, n_output_tokens])
        components = np.zeros((n_layers, n_components, n_output_tokens))
        models = []

        # Get rid of negative activation values
        # (There are some, because GPT2 uses GELU, which allow small negative values)
        self.activations = np.maximum(activations, 0).T

        self.model = decomposition.NMF(n_components=n_components,
                                  init='random',
                                  random_state=0,
                                  max_iter=500)
        self.components = self.model.fit_transform(self.activations).T


    @staticmethod
    def reshape_activations(activations,
                            from_layer: Optional[int] = None,
                            to_layer: Optional[int] = None,
                            collect_activations_layer_nums: Optional[List[int]] = None):
        """Prepares the activations tensor for NMF by reshaping it from four dimensions
        (batch, layer, neuron, position) down to two:
        ( neuron (and layer), position (and batch) ).

        Args:
            activations (tensor): activations tensors of shape (batch, layers, neurons, positions) and float values
            from_layer (int or None): Start value. Used to indicate a range of layers whose activations are to
                be processed
            to_layer (int or None): End value. Used to indicate a range of layers
            collect_activations_layer_nums (list of ints or None): A list of layer IDs. Used to indicate specific
                layers whose activations are to be processed
        """

        if len(activations.shape) != 4:
            raise ValueError(f"The 'activations' parameter should have four dimensions: "
                             f"(batch, layers, neurons, positions). "
                             f"Supplied dimensions: {activations.shape}", 'activations')

        if collect_activations_layer_nums is None:
            collect_activations_layer_nums = list(range(activations.shape[1]))

        layer_nums_to_row_ixs = {layer_num: i
                                 for i, layer_num in enumerate(collect_activations_layer_nums)}

        if from_layer is not None or to_layer is not None:
            from_layer = from_layer if from_layer is not None else 0
            to_layer = to_layer if to_layer is not None else activations.shape[0]

            if from_layer == to_layer:
                raise ValueError(f"from_layer ({from_layer}) and to_layer ({to_layer}) cannot be the same value. "
                                 "They must be apart by at least one to allow for a layer of activations.")

            if from_layer > to_layer:
                raise ValueError(f"from_layer ({from_layer}) cannot be larger than to_layer ({to_layer}).")

            layer_nums = list(range(from_layer, to_layer))
        else:
            layer_nums = sorted(layer_nums_to_row_ixs.keys())

        if any([num not in layer_nums_to_row_ixs for num in layer_nums]):
            available = sorted(layer_nums_to_row_ixs.keys())
            raise ValueError(f"Not all layers between from_layer ({from_layer}) and to_layer ({to_layer}) "
                             f"have recorded activations. Layers with recorded activations are: {available}")

        row_ixs = [layer_nums_to_row_ixs[layer_num] for layer_num in layer_nums]
        activation_rows = [activations[:, row_ix] for row_ix in row_ixs]
        # Merge 'layers' and 'neuron' dimensions. Sending activations down from
        # (batch, layer, neuron, position) to (batch, neuron, position)

        merged_act = np.concatenate(activation_rows, axis=1)
        # merged_act = np.stack(activation_rows, axis=1)
        # 'merged_act' is now (batch, neuron (and layer), position)
        merged_act = merged_act.swapaxes(0, 1)
        # 'merged_act' is now (neuron (and layer), batch, position)
        merged_act = merged_act.reshape(merged_act.shape[0], -1)

        return merged_act

    def explore(self, input_sequence: int = 0, **kwargs):
        """
        Show interactive explorable for a single sequence with sparklines to isolate factors.

        Example:
            ![NMF Example](../../img/nmf_ex_1.png)
        Args:
            input_sequence: Which sequence in the batch to show.
        """
        tokens = []

        for idx, token in enumerate(self.tokens[input_sequence]):  # self.tokens[:-1]
            type = "input" if idx < self.n_input_tokens else 'output'
            tokens.append({'token': token,
                           'token_id': int(self.token_ids[input_sequence][idx]),
                           # 'token_id': int(self.token_ids[idx]),
                           'type': type,
                           # 'value': str(components[0][comp_num][idx]),  # because json complains of floats
                           'position': idx
                           })

        # If the sequence contains both input and generated tokens:
        # Duplicate the factor at index 'n_input_tokens'. THis way
        # each token has an activation value (instead of having one activation less than tokens)
        # But with different meanings: For inputs, the activation is a response
        # For outputs, the activation is a cause
        if len(self.token_ids[input_sequence]) != self.n_input_tokens:
            # Case: Generation. Duplicate value of last input token.
            factors = np.array(
                [np.concatenate([comp[:self.n_input_tokens], comp[self.n_input_tokens - 1:]]) for comp in
                  self.components])
            factors = [comp.tolist() for comp in factors]  # the json conversion needs this
        else:
            # Case: no generation
            factors = [comp.tolist() for comp in self.components]  # the json conversion needs this

        data = {
            # A list of dicts. Each in the shape {
            # Example: [{'token': 'by', 'token_id': 2011, 'type': 'input', 'position': 235}]
            'tokens': tokens,
            # Three-dimensional list. Shape: (1, factors, sequence length)
            'factors': [factors]
        }

        d.display(d.HTML(filename=os.path.join(self._path, "html", "setup.html")))
        d.display(d.HTML(filename=os.path.join(self._path, "html", "basic.html")))
        viz_id = 'viz_{}'.format(round(random.random() * 1000000))
        # print(data)
        js = """
         requirejs(['basic', 'ecco'], function(basic, ecco){{
            const viz_id = basic.init()
            ecco.interactiveTokensAndFactorSparklines(viz_id, {})
         }}, function (err) {{
            console.log(err);
        }})""".format(data)
        d.display(d.Javascript(js))

        if 'printJson' in kwargs and kwargs['printJson']:
            print(data)
            return data



    def plot(self, n_components=3):

        for idx, comp in enumerate(self.components):
            #     print('Layer {} components'.format(idx), 'Variance: {}'.format(lm.variances[idx][:n_components]))
            print('Layer {} components'.format(idx))
            comp = comp[:n_components, :].T

            #     plt.figure(figsize=(16,2))
            fig, ax1 = plt.subplots(1)
            plt.subplots_adjust(wspace=.4)
            fig.set_figheight(2)
            fig.set_figwidth(17)
            #     fig.tight_layout()
            # PCA Line plot
            ax1.plot(comp)
            ax1.set_xticks(range(len(self.tokens)))
            ax1.set_xticklabels(self.tokens, rotation=-90)
            ax1.legend(['Component {}'.format(i + 1) for i in range(n_components)], loc='center left',
                       bbox_to_anchor=(1.01, 0.5))

            plt.show()
