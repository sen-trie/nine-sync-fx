// Independent nested-loop diagnostic for an existing animated PMX.
float4x4 WVP : WORLDVIEWPROJECTION;
int Copies < string UIName = "Copies"; > = 2;
int Layers < string UIName = "Layers"; > = 3;
int Outer;
int Inner;
float Time : TIME;
struct Output { float4 position : POSITION; float id : TEXCOORD0; };
Output VS(float4 position : POSITION) {
    Output result;
    position.x += Outer * 0.4;
    position.y += Inner * 0.2 + Time * 0.1;
    result.position = mul(position, WVP);
    result.id = Outer + Inner * 10;
    return result;
}
float4 PS(Output input) : COLOR { return float4(input.id * 0.02, Outer * 0.3, 0.5, 1); }
technique Model < string MMDPass = "object";
    string Script = "LoopByCount=Copies;LoopGetIndex=Outer;LoopByCount=Layers;LoopGetIndex=Inner;Pass=Main;LoopEnd=;LoopEnd=;";
> {
    pass Main {
        VertexShader = compile vs_3_0 VS();
        PixelShader = compile ps_3_0 PS();
    }
}
